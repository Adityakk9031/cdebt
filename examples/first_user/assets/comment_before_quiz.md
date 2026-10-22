<!-- cdebt-gate -->
## cdebt: 18 AI-written lines not understood (advisory)

```text
pkg/pricing.py::apply_discount  5-12  NOT UNDERSTOOD (not_quizzed)
pkg/pricing.py::late_fee  15-21  NOT UNDERSTOOD (not_quizzed)
pkg/pricing.py::invoice_total  24-26  NOT UNDERSTOOD (not_quizzed)
```

Run `cdebt quiz --pr 1` locally to clear, then comment `/cdebt recheck`.

Attribution sources on changed AI lines: `git-ai`: 18. Policy: `--require default`. Advisory: this does not block the merge.

<sub>cdebt marks code, not people. A quiz is visibility, not a lie detector.</sub>
