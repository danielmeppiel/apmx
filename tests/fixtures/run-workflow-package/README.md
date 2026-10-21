# run.yml package-factory fixture

The `.github/workflows/run-selftest.yml` package job runs this directory as an
APM package (`danielmeppiel/apmx/tests/fixtures/run-workflow-package#<sha>`)
against `../run-workflow-workspace`, with the hermetic protocol actor: no
model is called.

`.apm/` is empty: APM requires a package to have primitives, dependencies or
an `.apm/` directory.
