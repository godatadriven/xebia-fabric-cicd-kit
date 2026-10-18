# Fabric repository template

Starting point for a Microsoft Fabric repository with CI/CD from [xebia-fabric-cicd-kit](../../README.md) (`fabkit`): a validate check on pull requests, a deploy on push to `dev`, `preprod` and `main`, and pre-commit hooks.

## Start

Copy this folder into a new repository:

```bash
git clone --depth 1 git@github.com:godatadriven/xebia-fabric-cicd-kit.git fabkit
mkdir my-repo
cp -R fabkit/templates/fabric-repo/. my-repo/
rm -rf fabkit
cd my-repo
git init -b main
git remote add origin <new remote url>
```

Push once the [setup in Azure and GitHub](../../README.md#setup-in-azure-and-github) is done, because a push to `main` deploys.

You need [uv](https://docs.astral.sh/uv/) and `make`. Then:

```bash
make setup                     # install pre-commit and its git hooks
make validate                  # the pull request check
make deploy ENVIRONMENT=DEV    # a deploy from your machine, after az login
```

`make help` lists the targets. The workflows call the same targets. The `fabkit` version is set in the `Makefile` and in `.pre-commit-config.yaml`; bump both by hand.

## Change first

- `config.yml`: replace the three `<workspace name> [...]` values with your workspace names.
- `config.yml`: set `item_types_in_scope` to the item types the repository deploys.
- `src/`: put the Fabric items here. Replacements per environment go in `src/parameter.yml`.
- `config.yml`: set `unpublish.skip` to `false` once `src/` has items, so items removed from the repository are removed from the workspace.
- Delete this README or replace it with your own.
