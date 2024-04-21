import re

import numpy as np
import pandas as pd

from midi_tokenizers.midi_tokenizer import MidiTokenizer


class NoLossTokenizer(MidiTokenizer):
    def __init__(
        self,
        eps: float = 0.001,
        n_velocity_bins: int = 128,
    ):
        super().__init__()
        self.eps = eps
        self.n_velocity_bins = n_velocity_bins
        self.specials = ["<CLS>"]
        self._build_vocab()
        self.token_to_id = {token: it for it, token in enumerate(self.vocab)}

        self.velocity_bin_edges = np.linspace(0, 127, num=n_velocity_bins, endpoint=True).astype(int)
        self.bin_to_velocity = self._build_velocity_decoder()
        self.token_to_id = {token: it for it, token in enumerate(self.vocab)}
        self.name = "NoLossTokenizer"

    def __rich_repr__(self):
        yield "NoLossTokenizer"
        yield "eps", self.eps
        yield "vocab_size", self.vocab_size

    @property
    def parameters(self):
        return {"eps": self.eps, "n_velocity_bins": self.n_velocity_bins}

    @property
    def vocab_size(self) -> int:
        return len(self.vocab)

    def _build_vocab(self):
        self.vocab = list(self.specials)

        self.token_to_velocity_bin = {}
        self.velocity_bin_to_token = {}

        self.token_to_pitch = {}
        self.pitch_to_on_token = {}
        self.pitch_to_off_token = {}

        self.token_to_dt = {}
        self.dt_to_token = []

        # Add MIDI note and velocity tokens to the vocabulary
        for pitch in range(21, 109):
            note_on_token = f"NOTE_ON_{pitch}"
            note_off_token = f"NOTE_OFF_{pitch}"

            self.vocab.append(note_on_token)
            self.vocab.append(note_off_token)

            self.token_to_pitch |= {note_on_token: pitch, note_off_token: pitch}
            self.pitch_to_on_token |= {pitch: note_on_token}
            self.pitch_to_off_token |= {pitch: note_off_token}

        for vel in range(self.n_velocity_bins):
            velocity_token = f"VELOCITY_{vel}"
            self.vocab.append(velocity_token)
            self.token_to_velocity_bin |= {velocity_token: vel}
            self.velocity_bin_to_token |= {vel: velocity_token}

        time_vocab, token_to_dt, dt_to_token = self._time_vocab()
        self.vocab += time_vocab

        self.token_to_dt = token_to_dt
        self.dt_to_token = dt_to_token
        self.max_time_value = self.token_to_dt[time_vocab[-1]]  # Maximum time

    def _time_vocab(self):
        time_vocab = []
        token_to_dt = {}
        dt_to_token = {}

        dt_it = 1
        dt = self.eps
        # Generate time tokens with exponential distribution
        while dt < 1:
            time_token = f"{dt_it}T"
            time_vocab.append(time_token)
            dt_to_token |= {dt: time_token}
            token_to_dt |= {time_token: dt}
            dt *= 2
            dt_it += 1
        return time_vocab, token_to_dt, dt_to_token

    def quantize_frame(self, df: pd.DataFrame):
        df["velocity_bin"] = np.digitize(df["velocity"], self.velocity_bin_edges) - 1
        return df

    def _build_velocity_decoder(self):
        self.bin_to_velocity = []
        for it in range(1, len(self.velocity_bin_edges)):
            velocity = (self.velocity_bin_edges[it - 1] + self.velocity_bin_edges[it]) / 2
            self.bin_to_velocity.append(int(velocity))
        return self.bin_to_velocity

    @staticmethod
    def _notes_to_events(notes: pd.DataFrame) -> list[dict]:
        """
        Convert MIDI note dataframe into a dict with on/off events.
        """
        note_on_df: pd.DataFrame = notes.loc[:, ["start", "pitch", "velocity_bin"]]
        note_off_df: pd.DataFrame = notes.loc[:, ["end", "pitch", "velocity_bin"]]

        note_off_df["time"] = note_off_df["end"]
        note_off_df["event"] = "NOTE_OFF"
        note_on_df["time"] = note_on_df["start"]
        note_on_df["event"] = "NOTE_ON"

        note_on_events = note_on_df.to_dict(orient="records")
        note_off_events = note_off_df.to_dict(orient="records")
        note_events = note_off_events + note_on_events

        note_events = sorted(note_events, key=lambda event: event["time"])
        return note_events

    def tokenize_time_distance(self, dt: float) -> list[str]:
        # Try filling the time beginning with the largest step
        current_step = self.max_time_value

        time_tokens = []
        filling_dt = 0
        current_step = self.max_time_value
        while True:
            if abs(dt - filling_dt) < self.eps:
                # Exit the loop when the gap is filled
                break
            if filling_dt + current_step - dt > self.eps:
                # Select time step that will fit into the gap
                current_step /= 2
            else:
                # Fill the gap with current time token
                time_token = self.dt_to_token[current_step]
                time_tokens.append(time_token)
                filling_dt += current_step

        return time_tokens

    def tokenize(self, notes: pd.DataFrame) -> list[str]:
        notes = self.quantize_frame(notes)
        tokens = []
        # Time difference between current and previous events
        previous_time = 0
        note_events = self._notes_to_events(notes=notes)

        for current_event in note_events:
            # Calculate the time difference between current and previous event
            dt = current_event["time"] - previous_time

            # Fill the time gap
            time_tokens = self.tokenize_time_distance(dt=dt)
            tokens += time_tokens

            event_type = current_event["event"]

            # Append note event tokens
            velocity = int(current_event["velocity_bin"])
            tokens.append(self.velocity_bin_to_token[velocity])
            pitch = int(current_event["pitch"])
            if event_type == "NOTE_ON":
                tokens.append(self.pitch_to_on_token[pitch])
            else:
                tokens.append(self.pitch_to_off_token[pitch])

            previous_time = current_event["time"]

        return tokens

    def fix_token_sequences(self, tokens: list[str]):
        """
        If there are NOTE_OFF tokens before NOTE_ON in the list,
        add NOTE_ON events at the beginning of the token list.
        If there are NOTE_ON tokens that are left without NOTE_OFF, add them at the end as well.
        """
        pressed = np.full(shape=(110), fill_value=-1)
        # There are velocity tokens before each event - we know with which velocity the key
        # we are releasing was played
        current_velocity_token = "VELOCITY_0"
        for token in tokens:
            if "VELOCITY" in token:
                current_velocity_token = token

            if "NOTE_ON" in token:
                velocity_bin = self.token_to_velocity_bin[current_velocity_token]
                pressed[self.token_to_pitch[token]] = velocity_bin

            if "NOTE_OFF" in token:
                pitch = self.token_to_pitch[token]
                if pressed[pitch] == -1:
                    tokens = [current_velocity_token, self.pitch_to_on_token[pitch]] + tokens
                pressed[pitch] = -1

        for key, state in enumerate(pressed):
            if state >= 0:
                note_off_token = self.pitch_to_off_token[key]
                velocity_token = self.velocity_bin_to_token[state]
                tokens = tokens + [velocity_token, note_off_token]

        return tokens

    def untokenize(self, tokens: list[str]) -> pd.DataFrame:
        tokens = self.fix_token_sequences(tokens=tokens)
        note_on_events = []
        note_off_events = []

        current_time = 0
        current_velocity = 0
        for token in tokens:
            if re.search(".T$", token) is not None:
                dt: float = self.token_to_dt[token]
                current_time += dt
            if "VELOCITY" in token:
                # velocity should always be right before NOTE_ON token
                current_velocity_bin = self.token_to_velocity_bin[token]
                current_velocity: int = self.bin_to_velocity[current_velocity_bin]
            if "NOTE_ON" in token:
                note = {
                    "pitch": self.token_to_pitch[token],
                    "start": current_time,
                    "velocity": current_velocity,
                }
                note_on_events.append(note)
            if "NOTE_OFF" in token:
                note = {
                    "pitch": self.token_to_pitch[token],
                    "end": current_time,
                }
                note_off_events.append(note)

        # Both should be sorted by time right now
        note_on_events = pd.DataFrame(note_on_events)
        note_off_events = pd.DataFrame(note_off_events)

        # So if we sort them by pitch ...
        note_on_events = note_on_events.sort_values(by="pitch", kind="stable").reset_index(drop=True)
        note_off_events = note_off_events.sort_values(by="pitch", kind="stable").reset_index(drop=True)

        # we get pairs of note on and note off events for each key-press
        notes = note_on_events
        notes["end"] = note_off_events["end"]

        notes = notes.sort_values(by="start")
        notes = notes.reset_index(drop=True)

        return notes
