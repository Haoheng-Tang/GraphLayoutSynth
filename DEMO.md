# Streamlit Demo

This spike branch includes a disposable local Streamlit UI for a demo.

Install demo dependencies:

```bash
python -m pip install -e ".[llm,demo]"
```

Run:

```bash
streamlit run demo_app.py
```

Example workflow:

1. Set the config path, number of candidates, top-k, seed, and output directory.
2. Click **Run generation**.
3. Inspect the ranking table.
4. Inspect top-k graph images and generated JSON/report paths.
5. Optionally enable Claude evaluation.

For Claude evaluation, create `.env.local` at the repo root:

```text
ANTHROPIC_API_KEY=your_api_key_here
```

This UI is read-only with respect to grammar rules. Edit YAML files directly if you need to change rule/config parameters.
