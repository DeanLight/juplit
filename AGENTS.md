<!-- ase-skills:begin -- copied from ase_skills_beta/ase_skills/consumer_agent.md; edit it there -->
## The ASE workspace

This repo follows the ASE Dev Workspace. Its skills, tools and settings live in the
`ase_skills_beta` repo, never in this one. Find an installed copy, in this order:

1. in this sandbox: a checkout named `ase_skills_beta` (for example `/home/user/ase_skills_beta`);
2. in this project's dependencies: `uv run ase-skills --help` answers;
3. beside this repo on this computer: `../ase_skills_beta`.

Run it from this repo's root as `uv run --project <checkout> ase-skills ...` (for 2,
`uv run ase-skills ...`). If there is none, tell the human; meanwhile
`uvx --from git+https://github.com/DeanLight/ase_skills_beta ase-skills ...` works.

Start with `ase-skills skills`, then `ase-skills show dev-workspace`, and follow its Step 0.
Brief a specialist with the same command. Add nothing of the workspace to this repo: no
config, no dependency, no MCP entry.
<!-- ase-skills:end -->
