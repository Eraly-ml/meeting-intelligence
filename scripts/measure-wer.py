#!/usr/bin/env python3
"""Compare a human reference with ASR text locally; JSON output never uploads it.

Case and punctuation are ignored; accents, Cyrillic letters and numerals are
preserved. WER alone does not measure correct owners, deadlines or diarization.
"""
import argparse
import json
from pathlib import Path
import re
import unicodedata


def words(text):
    return re.findall(r"[^\W_]+(?:['’][^\W_]+)*", unicodedata.normalize("NFC", text).casefold())


def score(reference, hypothesis):
    expected, actual = words(reference), words(hypothesis)
    if not expected:
        raise ValueError("A nonempty reference transcript is required")
    # Each cell retains distance and the S/D/I operation counts.
    previous = [(j, 0, 0, j) for j in range(len(actual) + 1)]
    for i, word in enumerate(expected, 1):
        current = [(i, 0, i, 0)]
        for j, heard in enumerate(actual, 1):
            if word == heard:
                current.append(previous[j - 1])
            else:
                sub, delete, insert = previous[j - 1], previous[j], current[j - 1]
                current.append(min((sub[0] + 1, sub[1] + 1, sub[2], sub[3]),
                                   (delete[0] + 1, delete[1], delete[2] + 1, delete[3]),
                                   (insert[0] + 1, insert[1], insert[2], insert[3] + 1)))
        previous = current
    errors, substitutions, deletions, insertions = previous[-1]
    return {"reference_words": len(expected), "hypothesis_words": len(actual), "errors": errors,
            "substitutions": substitutions, "deletions": deletions, "insertions": insertions,
            "wer": errors / len(expected)}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("reference", type=Path)
    parser.add_argument("hypothesis", type=Path)
    args = parser.parse_args()
    print(json.dumps(score(args.reference.read_text(), args.hypothesis.read_text()), indent=2))
