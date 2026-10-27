# Controllers

Fleet has three built-in pure controllers and a plugin host with pure admission;
the Gateway controller is a design. Each
guide keeps the controller's lifecycle, interface and state-machine diagram
together.

| Controller | Resource | Responsibility |
| --- | --- | --- |
| [Task](task/README.md) | [Task](../resources/task.md) | Runs and releases one agent session, and delivers its brief and inbox. |
| [ChangeRequest](change-request/README.md) | [ChangeRequest](../resources/change-request.md) | Mirrors one forge change and retains its numbered snapshot. |
| [Forge](forge/README.md) | [Task forge status](../resources/task.md#following-declared-changes) | Follows declared changes, relays feedback and plans mark-ready. |
| [Plugin](plugin/README.md) | [Plugin-defined kinds](../resources/plugin-kinds.md) | Hosts external controllers behind the same probe, plan and effect boundaries. |
| [Gateway](gateway/README.md) (design) | [Conversation](../resources/conversation.md), [Listener](../resources/listener.md), [Task gateway status](../resources/task.md#talking-in-a-conversation) | Planned: mirrors chat threads, ties each to one Task, relays messages in and declarations out. |

See the [overall architecture](../ARCHITECTURE.md) for the ports and crate
boundaries shared by the controllers and the plugin host.
