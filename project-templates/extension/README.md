# Extension template

```bash
pip install cookiecutter
cookiecutter project-templates/extension       # answer the prompts
cp -r my-extension /path/to/flexaflow/contrib/extensions/
cd /path/to/flexaflow && pytest contrib/extensions/my-extension/tests
python tools/build_packages.py                   # zip it and add it to packages/index.json
```

Then read `docs/EXTENSIONS.md` for what `register(api)` can do.
