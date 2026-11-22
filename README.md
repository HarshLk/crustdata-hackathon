# Penalty-Deduction Recovery (pdr)

Work in progress (ContextCon 05 build). The full README, data-source list and architecture diagram come later.

**Job.** Decide, for each penalty a client deducts from a 3PL's payment, whether the client's own agreement and the evidence allow it. Then recover what they don't. Each deduction gets one verdict (valid, disputable, cannot verify) with every fact cited; a dispute is sent exactly once, after a named person approves.

Design and contracts: `docs/context/` (start with `INTERFACES.md` and `W0_NOTES.md`).

## Run the tests

```bash
pip install -e .          # or: export PYTHONPATH=.
python -m pytest -q
```

Secrets go in `.env` (see `.env.example`); never commit it.
