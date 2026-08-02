# Example: bounded API fix

```bash
sera init
sera map

sera task new "reject invalid cursor" \
  --objective "Return HTTP 400 for malformed pagination cursors without changing valid pagination" \
  --mode standard \
  --risk medium \
  --uncertainty 1 \
  --file src/api/pagination.py \
  --file tests/test_pagination.py \
  --constraint "Preserve the public response schema" \
  --verify "python -m unittest tests.test_pagination"

sera route
sera packet build
```

After implementation:

```bash
python -m unittest tests.test_pagination > test-output.txt 2>&1
sera record \
  --command "python -m unittest tests.test_pagination" \
  --exit-code 0 \
  --summary "Pagination suite passed" \
  --output-file test-output.txt

sera packet review
```
