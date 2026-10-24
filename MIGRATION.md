# Migration to google-genai

## Important: Package Conflict Resolution

If you're upgrading from the old `google-generativeai` package to the new `google-genai` package, you **must uninstall the old package first** to avoid import conflicts.

### On Your Server, Run:

```bash
pip uninstall google-generativeai -y
pip install google-genai
```

Or with the requirements file:

```bash
pip uninstall google-generativeai -y
pip install -r requirements.txt
```

### Why This Is Necessary

Both packages use the `google` namespace, and having both installed simultaneously causes import errors like:

```
ImportError: cannot import name 'genai' from 'google' (unknown location)
```

The old package creates `google.generativeai` while the new package creates `google.genai`. Having both installed confuses Python's import system.

### Verification

After installation, verify with:

```bash
pip list | grep google
```

You should see `google-genai` but **NOT** `google-generativeai`.
