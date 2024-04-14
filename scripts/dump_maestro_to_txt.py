from concurrent.futures import ThreadPoolExecutor

import pandas as pd
from datasets import load_dataset

from midi_tokenizers.one_time_tokenizer import OneTimeTokenizer


def main():
    dataset = load_dataset("roszcz/maestro-sustain-v2", split="train")
    eps = 0.015
    n_velocity_bins = 32
    tokenizer = OneTimeTokenizer(eps=eps, n_velocity_bins=n_velocity_bins)

    def process_record(record):
        notes = pd.DataFrame(record["notes"])
        tokens = tokenizer.tokenize(notes=notes)
        return " ".join(str(token) for token in tokens) + "\n"

    with open(f"data/maestro-tokenized-one-time-eps-{eps}.txt", "w+") as file, ThreadPoolExecutor() as executor:
        # Process records concurrently
        for result in executor.map(process_record, dataset):
            file.write(result)


if __name__ == "__main__":
    main()
