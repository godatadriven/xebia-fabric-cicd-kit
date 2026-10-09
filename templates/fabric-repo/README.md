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

Run `pre-commit install` to enable the hooks. The `fabkit` version is pinned in `validate.yml`, `deploy.yml` and `.pre-commit-config.yaml`, and is bumped by hand.

## Change first

- `config.yml`: replace the three `<workspace name> [...]` values with your workspace names.
- `config.yml`: set `item_types_in_scope` to the item types the repository deploys.
- `src/`: put the Fabric items here. Replacements per environment go in `src/parameter.yml`.
- Delete this README or replace it with your own.
