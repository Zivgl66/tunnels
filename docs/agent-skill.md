# Using tunnels from an AI agent

`skills/tunnels/SKILL.md` teaches a coding agent to open a tunnel and reach a
cluster through it, instead of guessing at ports or editing your kubeconfig.

It is a plain markdown file in the
[Agent Skills](https://agentskills.io) format, so it works in any client that
reads them. There is nothing to install beyond `tunnels` itself.

## Install it

**Claude Code**, for every project:

```bash
mkdir -p ~/.claude/skills/tunnels
curl -fsSL https://raw.githubusercontent.com/Zivgl66/tunnels/main/skills/tunnels/SKILL.md \
  -o ~/.claude/skills/tunnels/SKILL.md
```

Drop the `~` for a single project: `.claude/skills/tunnels/SKILL.md`.

Restart the session afterwards. Skills load at start-up, so one added
mid-session is not picked up.

**Other clients** read skills from their own directory. Copy the same file
there, or point `npx skills add` at this repo:

```bash
npx skills add Zivgl66/tunnels --skill tunnels
```

## Use it

Ask for what you want, not for the commands:

```
get the pods in the platform cluster on dev
```

The agent reads your config, brings up `dev`, and runs `kubectl` against
`tunnels-dev-platform`. In Claude Code you can also invoke it directly:

```
/tunnels dev
```

## What it stops the agent doing

These are the failures the skill exists to prevent, all of them seen in
practice:

- **Inventing environment names.** It reads your config first. Environment and
  target names come from the file, never from a guess.
- **Hardcoding a port.** Local ports are picked at startup and change between
  runs, so the skill reads the live port from `tunnels status` or
  `~/.tunnels/state.json` every time.
- **Editing the kubeconfig.** `tunnels up` writes `tls-server-name`, which is
  what keeps the certificate valid over localhost. A hand-edited kubeconfig
  breaks it.
- **Forgetting `--context`.** Without it kubectl uses whatever context is
  current, which is usually the wrong cluster.
- **Tearing down tunnels you are using.** The skill does not run
  `tunnels down` unless you ask.

## What it will not do

It will not create or edit your config. `tunnels init` and `tunnels discover`
are yours to run: they decide which accounts and jump hosts an agent can
reach, and that is not a decision to delegate.

It also cannot install `tunnels`, log you into AWS, or approve an SSO browser
prompt.
