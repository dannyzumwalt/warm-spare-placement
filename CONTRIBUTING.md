# Contributing

## Workflow

1. Create a branch from `main`.
2. Make focused changes with tests where applicable.
3. Run the relevant checks before opening a pull request.
4. Open a pull request with a clear summary, test evidence, and any input or config assumptions.

## Development

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e '.[plotting]'
python -m unittest discover -s tests
```

## Project-specific notes

- Do not commit real office input files or scenario matrices.
- Keep configuration changes in `config/default.yaml` backward-compatible unless the pull request clearly documents the migration.
- Preserve canonical `office_id` ordering assumptions across validation, preprocessing, and reporting.

## Pull request checklist

- Scope is limited to a coherent change.
- Tests were added or updated when behavior changed.
- Documentation was updated when interfaces, config, or outputs changed.
- Generated files, local environments, and sensitive input data are not included in the diff.
