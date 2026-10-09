<p align="center"><img src="docs/logo.svg" alt="fabkit logo" width="160"></p>

# xebia-fabric-cicd-kit

xebia-fabric-cicd-kit (fabkit) is a wrapper CI/CD tool for Microsoft Fabric, built on [fabric-cicd](https://github.com/microsoft/fabric-cicd).

Use it in multi-repo setups that need standardized CI/CD across deployments.

It extends item validation with custom checks, pre-commit hooks and extensions to fabric-cicd.

To onboard, start a new Fabric repository from the [template](templates/fabric-repo/).

At the moment, GitHub is the only supported platform.

## How it works

fabkit is built around fabric-cicd's config-based deploy, `deploy_with_config`. The deployment settings stay in fabric-cicd's own `config.yml` (workspace per environment, repository directory, item types, publish and unpublish) and `parameter.yml`. fabkit has no config file of its own. Its few extra settings, such as uploads, are command flags in the workflow.

fabkit has two commands:

- `fabkit validate` checks the repository files for every environment, without logging in to Fabric. Use it as the pull request check.
- `fabkit deploy` runs the same checks for one environment, verifies the connections against Fabric, calls `deploy_with_config`, and fails on the warnings fabric-cicd logs while reporting success.

Checks fabkit adds to fabric-cicd:

| Problem | fabric-cicd | fabkit |
|---|---|---|
| Environment key missing or with the wrong casing in `parameter.yml` | Skips the replacement | `validate` fails |
| Placeholder (`TODO`, `<...>`) left in a value | Deploys it as written | `validate` fails |
| Value shaped unlike its `find_value`, e.g. a GUID with a character missing | Deploys it | `validate` fails |
| Same value in every environment | Deploys it | `validate` fails; use `_ALL_` if intended |
| `find_value` that matches no file | Replaces nothing | `validate` fails |
| `parameter.yml` that `config.yml` does not reference | Ignores the file | `validate` fails |
| Two items with the same `logicalId` | One overwrites the other | `validate` fails |
| VariableLibrary override with keys other than `name` and `value` | Fabric rejects the library at publish | `validate` fails |
| Connection ID the service principal cannot see | Publishes the item unbound and reports success | `deploy` stops before publishing |
| Binding warning during the deploy | Reports success | `deploy` fails |

## Getting started

Start a new repository by copying the [template](templates/fabric-repo/) (its README has the commands), or install the command in an existing one:

```bash
uvx --from xebia-fabric-cicd-kit==0.1.0 fabkit validate
```

fabkit reads fabric-cicd's `config.yml`:

```yaml
core:
  workspace:
    DEV: "Sales [dev]"
    PREPROD: "Sales [preprod]"
    PROD: "Sales [prod]"
  repository_directory: src
  parameter: src/parameter.yml
  item_types_in_scope: [Notebook, DataPipeline, VariableLibrary]
unpublish:
  skip: false
```

On top of fabric-cicd's own rules, fabkit requires two things:

- Every environment maps to a workspace (`core.workspace` or `core.workspace_id`). The keys are the environment names, and the GitHub Environments must use the same names.
- `unpublish.skip` is set explicitly. With `false`, workspace items that are not in the repository are removed, which is also fabric-cicd's default.

## Usage

### `fabkit validate`

Checks the repository for every environment in `config.yml`, without logging in to Fabric. Run it from the repository root.

```bash
fabkit validate
fabkit validate --config deploy/config.yml --unmatched-find-value warning
```

A value that cannot exist yet, such as the GUID of a production lakehouse that is not created, can be written as `PLACEHOLDER`. `validate` warns about it; `deploy` fails when it would reach the environment being deployed. This works in `find_replace`, `key_value_replace` and `spark_pool`; fabric-cicd rejects a `connection_id` that is not a GUID.

### `fabkit deploy`

Deploys one environment:

```bash
fabkit deploy --environment PROD
```

In order, it:

1. Runs the config, parameter, `logicalId` and value-set checks for that environment, and checks that every upload source exists. Nothing is published when one fails.
2. Logs in with `DefaultAzureCredential`: the `azure/login` step in CI, or `az login` locally.
3. Checks that every connection the deploy binds is visible to the service principal. This covers `semantic_model_binding` in `parameter.yml` and variable-library variables whose name matches `--connection-variables` (default `ConnectionID$`).
4. Deploys with fabric-cicd's `deploy_with_config`. It fails on any fabric-cicd error and on the binding warnings fabric-cicd logs while reporting success.
5. Runs the uploads, unless the deploy failed.

Set `RUNNER_DEBUG=1` for fabric-cicd's debug logging; GitHub sets it on a debug re-run.

### Uploads

For notebooks that import shared Python code from a lakehouse, `--upload` copies a local file or folder into a lakehouse's `Files/` after publishing:

```bash
fabkit deploy --environment PROD --upload "lib/=<lakehouse GUID>:Files/_lib/lib/"
```

The lakehouse is given by its GUID; the workspace is the one being deployed. `__pycache__` folders are skipped. Uploads overwrite files but never delete: a file removed from the local folder stays in the lakehouse.

## Pre-commit hooks

The `logicalId` and value-set checks also run as pre-commit hooks:

```yaml
- repo: https://github.com/godatadriven/xebia-fabric-cicd-kit
  rev: v0.1.0
  hooks:
    - id: fabkit-logical-ids
    - id: fabkit-value-sets
```

Pin an exact tag: pre-commit caches the hook per `rev`.

## GitHub Actions

The [template](templates/fabric-repo/) has both workflows. A pull request runs `validate`:

```yaml
- run: uvx --from xebia-fabric-cicd-kit==0.1.0 fabkit validate
```

A push to `dev`, `preprod` or `main` deploys to the matching environment:

```yaml
permissions:
  id-token: write
  contents: read

jobs:
  deploy:
    runs-on: ubuntu-latest
    environment: ${{ github.ref_name == 'main' && 'PROD' || github.ref_name == 'preprod' && 'PREPROD' || 'DEV' }}
    steps:
      - uses: actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1 # v7.0.1
      - uses: astral-sh/setup-uv@c18668ad3cf93ea998bef934396af7bb5c839dc7 # v10.2.0
      - uses: azure/login@a641126d1b8aa4d1fa005f4f92df94a3a4c4c906 # v3.1.0
        with:
          client-id: ${{ secrets.AZURE_CLIENT_ID }}
          tenant-id: ${{ secrets.AZURE_TENANT_ID }}
          allow-no-subscriptions: true
      - name: Deploy
        env:
          ENVIRONMENT: ${{ github.ref_name == 'main' && 'PROD' || github.ref_name == 'preprod' && 'PREPROD' || 'DEV' }}
        run: uvx --from xebia-fabric-cicd-kit==0.1.0 fabkit deploy --environment "${ENVIRONMENT}"
```

### Setup in Azure and GitHub

1. Create an Entra app registration for deploys.
2. Add one federated credential per GitHub Environment. Take the subject from the `azure/login` error of a first, failing run; newer repositories use a subject format that is hard to predict.
3. Create the GitHub Environments, named like the `config.yml` keys, with the secrets `AZURE_CLIENT_ID` and `AZURE_TENANT_ID`. Add protection rules on production.
4. Give the service principal Contributor or higher on each workspace.
5. Share every connection the deploy binds with the service principal.

## Versioning

Versions below 1.0 may change flags between minor releases. Pin an exact version and update it by hand, in the workflow `run:` commands and the pre-commit `rev`.

On Dependabot pull requests the canary has no access to environment secrets; run it by hand with workflow_dispatch on the Dependabot branch.

## License

MIT
