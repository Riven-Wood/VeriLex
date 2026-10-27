//! The native notification library accepts application identity only once.
//! Share the first result across calendar, task and manual notifications.
use std::sync::OnceLock;

pub struct NotificationIdentity(OnceLock<Result<(), String>>);

impl NotificationIdentity {
    pub const fn new() -> Self {
        Self(OnceLock::new())
    }

    pub fn ensure(&self, initialize: impl FnOnce() -> Result<(), String>) -> Result<(), String> {
        self.0.get_or_init(initialize).clone()
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::sync::{
        atomic::{AtomicUsize, Ordering},
        Barrier,
    };

    #[test]
    fn subsequent_notifications_reuse_successful_identity() {
        let identity = NotificationIdentity::new();
        assert_eq!(identity.ensure(|| Ok(())), Ok(()));
        // Mirrors the native library: a second initialization is rejected.
        assert_eq!(identity.ensure(|| Err("AlreadySet".into())), Ok(()));
    }

    #[test]
    fn failed_initialization_is_not_reported_as_success() {
        let identity = NotificationIdentity::new();
        assert_eq!(
            identity.ensure(|| Err("CouldNotSet".into())),
            Err("CouldNotSet".into())
        );
        assert_eq!(identity.ensure(|| Ok(())), Err("CouldNotSet".into()));
    }

    #[test]
    fn concurrent_notification_sources_initialize_only_once() {
        let identity = NotificationIdentity::new();
        let calls = AtomicUsize::new(0);
        let barrier = Barrier::new(8);
        std::thread::scope(|scope| {
            let handles: Vec<_> = (0..8)
                .map(|_| {
                    scope.spawn(|| {
                        barrier.wait();
                        identity.ensure(|| {
                            if calls.fetch_add(1, Ordering::SeqCst) == 0 {
                                Ok(())
                            } else {
                                Err("AlreadySet".into())
                            }
                        })
                    })
                })
                .collect();
            for handle in handles {
                assert_eq!(handle.join().unwrap(), Ok(()));
            }
        });
        assert_eq!(calls.load(Ordering::SeqCst), 1);
    }
}
