# Contributing

Contributions are welcome through GitHub issues and pull requests.

Before submitting a change:

1. Create a Python 3.11 virtual environment and install `requirements.txt`.
2. Run `python -m unittest discover -s tests -v`.
3. Keep scientific changes separate from reporting or interface changes.
4. Increment pipeline/schema versions when a change affects scientific outputs
   or feature semantics, and run a new frozen benchmark instead of overwriting an
   earlier evaluation.
5. Do not describe unmatched signals as discoveries or confirmed planets.

Large MAST caches, training datasets, trained models and generated results must not
be committed. Reproduction commands and compact benchmark artifacts are preferred.
