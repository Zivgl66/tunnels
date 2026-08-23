# Troubleshooting

| What you see | What it means | What to do |
| --- | --- | --- |
| `no config file found` | No config yet | `tunnels init` |
| `no running instance with tag ...` | The tag matches nothing running | Check the tag with the `describe-instances` command in the [quick start](../README.md#quick-start) |
| `warning: 9 instances match ...` | The tag matches a node group | Fine. It uses the first one. Use a narrower tag to be exact |
| `port <n> never opened` | The jump host cannot reach the target, or has no SSM agent | Read the log tail printed with the error, and `~/.tunnels/logs/` |
| `port 9443 is busy (com.docker.backend)` | Another program owns that port | Remove `local_port` and let the tool choose |
| `certificate is valid for ...` from kubectl | The kubeconfig patch did not land | `tunnels down <env> && tunnels up <env>` |
| A tunnel shows grey in the label | The session dropped | `tunnels up <env>` again |
| Tunnels die after ~20 minutes unused | The account's SSM `idleSessionTimeout` | `tunnels up <env> --keepalive`. See [configuration](configuration.md#idle-timeouts) |
| SSO opens a browser every time | The token is genuinely expiring | Normal. It only logs in when the cached token has gone |

## Known limits

- No auto-reconnect. A dropped session shows grey until you run `up` again.
- `down` leaves the kubectl context in place. It fails loudly if you use it,
  and `up` repairs it.
- Sessions killed outside the tool stay `Connected` on the AWS side until they
  time out. `tunnels doctor --fix` closes them.
- The label sits in the top right corner and cannot be moved.
- macOS only. The label uses Cocoa, because the system Tk on macOS 26 no longer
  draws windows at all.
