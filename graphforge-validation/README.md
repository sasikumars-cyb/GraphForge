# graphforge-validation

GraphForge's permanent regression validation framework — runs the
24-repository validation suite through GraphForge's real APIs and checks
the result against a captured baseline. See
[`docs/validation-guide.md`](docs/validation-guide.md) for how to run it,
what each of the ten validations checks, how to update the expected-state
fixtures after a legitimate GraphForge change, and the real gaps this
framework discovered while being built.

```bash
cd graphforge-validation
pip install -r requirements.txt
python scripts/run_validation.py
```
