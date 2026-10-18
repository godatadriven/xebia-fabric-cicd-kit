<p align="center"><img src="docs/logo.svg" alt="fabkit logo" width="160"></p>

# xebia-fabric-cicd-kit

xebia-fabric-cicd-kit (fabkit) is a wrapper CI/CD tool for Microsoft Fabric, built on [fabric-cicd](https://github.com/microsoft/fabric-cicd).

Use it in multi-repo setups that need standardized CI/CD across deployments.

It extends item validation with custom checks, pre-commit hooks and extensions to fabric-cicd.

To onboard, start a new Fabric repository from the [template](templates/fabric-repo/).

At the moment, GitHub is the only supported platform. Private-link workspaces are not supported: fabkit's own API calls and uploads use the public Fabric, Power BI and OneLake endpoints.

## How it works

fabkit is built around fabric-cicd's config-based deploy, `deploy_with_config`. The deployment settings stay in fabric-cicd's own `config.yml` (workspace per environment, repository directory, item types, publish and unpublish) and `parameter.yml`. fabkit has no config file of its own. Its few extra settings, such as uploads, are command flags in the workflow.

fabkit has three commands:

- `fabkit validate` checks the repository files for every environment, without logging in to Fabric. Use it as the pull request check.
- `fabkit deploy` runs the same checks for one environment, verifies the connections against Fabric, calls `deploy_with_config`, and fails on the binding and value-set warnings fabric-cicd logs while reporting success.
- `fabkit upload` replaces a folder in a lakehouse with a local folder, such as a dbt project, without a Fabric deploy.

Checks fabkit adds to fabric-cicd:

| Problem | fabric-cicd | fabkit |
|---|---|---|
| Environment key missing or with the wrong casing in `parameter.yml` | Skips the replacement | `validate` fails |
| Placeholder (`TODO`, `<...>`) left in a value | Deploys it as written | `validate` fails |
| Value shaped unlike its `find_value`, e.g. a GUID with a character missing | Deploys it | `validate` fails |
| Same value in every environment | Deploys it | `validate` fails; use `_ALL_` if intended |
| `find_value` that matches no file its `item_type`, `item_name` or `file_path` filters select, in any environment | Replaces nothing | `validate` fails |
| `parameter.yml` that `config.yml` does not reference | Ignores the file | `validate` fails |
| Per-environment setting in `config.yml`, such as `item_types_in_scope` or `unpublish.exclude_regex`, that leaves out an environment | Ignores the setting there, e.g. deploys every item type | `validate` fails |
| No items in scope while `unpublish.skip` is `false` | Removes every item of the types in scope from the workspace | `validate` fails |
| Two items with the same `logicalId` | Fails at deploy, after login; allows a shared all-zero placeholder | `validate` fails |
| Two items with the same type and name in different folders | Deploys only one of them | `validate` fails |
| VariableLibrary override with keys other than `name` and `value` | Fabric rejects the library at publish | `validate` fails |
| VariableLibrary without a value set for an environment | Activates the default value set | `validate` fails; add an empty value set to keep the defaults |
| `semantic_model_binding` to a model that is not in the repository | Skips the binding | `validate` fails |
| Error logged while validating `parameter.yml`, e.g. an unknown `item_type` | Skips the replacement | `validate` fails |
| Connection ID the service principal cannot see | Publishes the item unbound and reports success | `deploy` stops before publishing |
| Binding, value-set or unpublish warning during the deploy | Reports success | `deploy` fails |
| Semantic model owned by someone other than the deploy identity | `semantic_model_binding` fails: only the owner can bind | `deploy --take-over-semantic-models` makes the deploy identity owner first |

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

On top of fabric-cicd's own rules, fabkit requires three things:

- Every environment maps to a workspace (`core.workspace` or `core.workspace_id`). The keys are the environment names, and the GitHub Environments must use the same names.
- `unpublish.skip` is set explicitly. With `false`, workspace items that are not in the repository are removed, which is also fabric-cicd's default. A repository with no items of the types in scope must use `true`.
- A per-environment setting names every environment. This covers `features`, each of the `constants`, `core.item_types_in_scope` and every `publish` and `unpublish` setting.

## Usage

### `fabkit validate`

Checks the repository for every environment in `config.yml`, without logging in to Fabric. Run it from the repository root.

```bash
fabkit validate
fabkit validate --config deploy/config.yml --unmatched-find-value warning
```

A value that cannot exist yet, such as the GUID of a production lakehouse that is not created, can be written as `PLACEHOLDER`. `validate` warns about it; `deploy` fails when it would reach the environment being deployed. This works in `find_replace`, `key_value_replace` and `spark_pool`; fabric-cicd rejects a `connection_id` that is not a GUID.

A `$ENV:` value needs its environment variable only in the deploy of its own environment. `validate` runs without it, and applies the `features` in `config.yml` as fabric-cicd's deploy does. A `$ENV:` value in a `connection_id` is the exception: fabric-cicd checks the connection GUID of every environment, so each deploy needs the variables of all environments. Use one variable under `_ALL_` to avoid that.

The checks also cover the templates listed under `extend` in `parameter.yml`.

### `fabkit deploy`

Deploys one environment:

```bash
fabkit deploy --environment PROD
```

In order, it:

1. Runs the config, parameter, `logicalId` and value-set checks for that environment, and checks that every upload source exists. Nothing is published when one fails.
2. Logs in with `DefaultAzureCredential`: the `azure/login` step in CI, or `az login` locally.
3. Checks that every connection the deploy binds is visible to the service principal. This covers `semantic_model_binding` in `parameter.yml` and variable-library variables whose name matches `--connection-variables` (default `ConnectionID$`).
4. With `--take-over-semantic-models`, makes the deploy identity owner of the repository's semantic models. See [Semantic model takeover](#semantic-model-takeover).
5. Deploys with fabric-cicd's `deploy_with_config`. It fails on any fabric-cicd error and on the binding and value-set warnings fabric-cicd logs while reporting success.
6. Runs the uploads, unless the deploy failed.

Set `RUNNER_DEBUG=1` for fabric-cicd's debug logging; GitHub sets it on a debug re-run.

### Uploads

An upload replaces a folder in a lakehouse's `Files/` with a local file or folder. Notebooks then read it from there, for example shared Python code or a dbt project.

`fabkit upload` does this on its own, for a repository without Fabric items, such as a dbt repository:

```bash
fabkit upload --environment PROD --lakehouse lh_landing --source dbt/ --target Files/dbt
```

`--upload` does the same after a deploy publishes:

```bash
fabkit deploy --environment PROD --upload "lib/=lh_landing:Files/_lib/lib/"
```

The lakehouse is given by name, or by GUID. A name is looked up in the environment's workspace from `config.yml`. `fabkit upload` needs only that workspace mapping, so a dbt repository's `config.yml` can be:

```yaml
core:
  workspace:
    DEV: "Sales [dev]"
    PROD: "Sales [prod]"
```

The target folder ends up holding exactly the uploaded files; files removed from the local folder are removed from the lakehouse too. The files go to `<target>.staging` first, and two renames then swap it into place. A failed upload leaves the target folder untouched. A run that starts during the swap can find the folder missing for a moment, so don't schedule runs to overlap with uploads.

Every upload writes a `.fabkit-upload` file into its folder. A folder that has files but no `.fabkit-upload` is not replaced, so a wrong target can't delete data that fabkit did not upload. To replace such a folder once, for example one filled by an older fabkit version, pass `--replace-existing` to `fabkit upload` or `--upload-replace-existing` to `fabkit deploy`.

`__pycache__` and `.git` folders are always left out. `--exclude` leaves out more: a glob over paths relative to the source, where a pattern that matches a folder leaves out everything in it. For a dbt project, a source with `dbt_project.yml`, `target`, `logs`, `profiles.yml` and `.user.yml` are left out as well. fabkit warns when a dbt project has `packages.yml` but no `dbt_packages/`: run `dbt deps` before the upload, or the runner needs network access to install the packages.

A step in a dbt repository's workflow, after `dbt deps` and a green `dbt build`:

```yaml
- name: Upload the dbt project
  env:
    ENVIRONMENT: ${{ github.ref_name == 'main' && 'PROD' || 'DEV' }}
  run: uvx --from xebia-fabric-cicd-kit==0.1.0 fabkit upload --environment "${ENVIRONMENT}" --lakehouse lh_landing --source dbt/ --target Files/dbt
```

### Semantic model takeover

Only the owner of a semantic model can bind its connection. A model that someone took over in the portal is owned by that person, and fabric-cicd's `semantic_model_binding` then fails with "you are not the owner of the semantic model". `--take-over-semantic-models` makes the deploy identity the owner again before publishing:

```bash
fabkit deploy --environment PROD --take-over-semantic-models
```

It covers the semantic models in the repository, matched to the workspace by the `displayName` in `.platform`. Models not yet in the workspace are skipped, and so are models that `publish.skip`, `publish.exclude_regex` or `publish.items_to_include` keep from publishing. With `publish.folder_exclude_regex` or `publish.folder_path_to_include` set, nothing is taken over and a warning says so. A failed takeover prints a warning and the deploy continues.

The deploy identity needs Member or Admin on the workspace, and the tenant setting that lets service principals use Power BI APIs. Other item types have no generally available takeover API.

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

The [template](templates/fabric-repo/) has both workflows, which call its `Makefile`. Without the template, a pull request runs `validate`:

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
        with:
          persist-credentials: false
      - uses: astral-sh/setup-uv@1c37ad07a6a961277cf70c0d37d6f313000f5884 # v10.3.0
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
