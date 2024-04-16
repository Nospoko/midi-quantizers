from datasets import load_dataset

from midi_tokenizers.one_time_tokenizer import OneTimeTokenizer
from midi_trainable_tokenizers.bpe_tokenizer import BpeTokenizer


def main():
    eps = 0.01
    n_velocity_bins = 32
    base_tokenizer = OneTimeTokenizer(eps=eps, n_velocity_bins=n_velocity_bins)
    tokenizer = BpeTokenizer(base_tokenizer=base_tokenizer)
    dataset = load_dataset("roszcz/maestro-sustain-v2", split="train")

    tokenizer.train(dataset)
    tokenizer.save_tokenizer(f"dumps/bpe-{base_tokenizer.name}-{eps}-{n_velocity_bins}.json")


if __name__ == "__main__":
    main()