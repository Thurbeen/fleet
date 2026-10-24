//! Fleet's controllers: pure functions from what is stored and observed to a
//! [`fleet_api::plan::Plan`]. No I/O happens here; the manager performs the
//! plan through the adapters.
//!
//! - [`TaskController`] runs one agent session for one brief.
//! - [`ChangeRequestController`] mirrors one change on a forge.
//!
//! Adapters sit above this crate and are unreachable from it, which the
//! compiler enforces:
//!
//! ```compile_fail
//! // A controller cannot reach the file store...
//! use fleet_store::FileStore;
//! ```
//!
//! ```compile_fail
//! // ...nor a runtime adapter...
//! use fleet_runtime::FakeRuntime;
//! ```
//!
//! ```compile_fail
//! // ...nor a forge adapter...
//! use fleet_forge::FakeForge;
//! ```
//!
//! ```compile_fail
//! // ...nor the manager that schedules it...
//! use fleet_manager::Manager;
//! ```
//!
//! ```compile_fail
//! // ...nor start a process.
//! use fleet_exec::run;
//! ```
//!
//! The positive control: the port types it decides over are in scope.
//!
//! ```
//! use fleet_controllers::{ChangeRequestController, TaskController};
//! assert_eq!(TaskController::KIND, "Task");
//! assert_eq!(ChangeRequestController::KIND, "ChangeRequest");
//! ```

pub mod change_request;
pub mod task;

pub use change_request::{ChangeRequestController, ChangeRequestInput, ChangeRequestObservations};
pub use task::{MAX_ATTEMPTS, RETRY_DELAY_MS, TaskController, TaskInput};
