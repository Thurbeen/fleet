# Controllers

Fleet has three pure controllers. Each guide keeps the controller's lifecycle,
interface and state-machine diagram together.

| Controller | Resource | Responsibility |
| --- | --- | --- |
| [Task](task/README.md) | [Task](../resources/task.md) | Runs and releases one agent session, and delivers its brief and inbox. |
| [ChangeRequest](change-request/README.md) | [ChangeRequest](../resources/change-request.md) | Mirrors one forge change and retains its numbered snapshot. |
| [Forge](forge/README.md) | [Task forge status](../resources/task.md#following-declared-changes) | Follows declared changes, relays feedback and plans mark-ready. |

See the [overall architecture](../ARCHITECTURE.md) for the ports and crate
boundaries shared by all three controllers.
