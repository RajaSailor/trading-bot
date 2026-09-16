# Contributing

## Code Style
- Follow existing Python style and keep changes focused/surgical.
- Add concise docstrings where behavior is non-obvious.

## Testing Requirements
- Add/adjust targeted tests for modified behavior.
- Run only relevant tests locally before submitting.

## Pull Request Process
1. Create focused commits with clear messages.
2. Include validation results and any rollout notes.
3. Address review feedback and rerun impacted tests.

## Development Setup
```bash
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt
pytest -q
```
