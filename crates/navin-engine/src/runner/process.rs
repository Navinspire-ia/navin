//! Spawn a shell command in its own process group with logs on disk.

use anyhow::{Context, Result};
use std::path::{Path, PathBuf};
use std::process::Stdio;
use std::time::Duration;

use crate::shadow::sandbox::{self, SandboxLimits};

use super::logs::open_log;

pub struct SupervisedProcess {
    child: tokio::process::Child,
    pub pid: u32,
    pub log_path: PathBuf,
}

impl SupervisedProcess {
    /// Spawn `command_line` via the shell in `dir`, stdout+stderr to a log
    /// file, its own process group so the whole tree can be killed.
    pub fn spawn(
        command_line: &str,
        dir: &Path,
        log_path: &Path,
        limits: Option<SandboxLimits>,
    ) -> Result<Self> {
        let log = open_log(log_path)?;
        let log_err = log.try_clone().context("cannot clone log handle")?;

        let mut cmd = shell_command(command_line);
        cmd.current_dir(dir)
            .stdin(Stdio::null())
            .stdout(Stdio::from(log))
            .stderr(Stdio::from(log_err));
        #[cfg(unix)]
        {
            use std::os::unix::process::CommandExt;
            // New process group: children (npm → node → workers) die with it.
            cmd.process_group(0);
        }
        if let Some(limits) = limits {
            sandbox::apply(&mut cmd, limits);
        }

        let child = tokio::process::Command::from(cmd)
            .kill_on_drop(true)
            .spawn()
            .with_context(|| format!("cannot spawn: {command_line}"))?;
        let pid = child.id().context("spawned process has no pid")?;
        Ok(SupervisedProcess { child, pid, log_path: log_path.to_path_buf() })
    }

    /// None while running, exit code once finished.
    pub fn try_exit_code(&mut self) -> Result<Option<i32>> {
        Ok(self.child.try_wait()?.map(|status| status.code().unwrap_or(-1)))
    }

    /// SIGTERM the group, give it a grace period, then SIGKILL.
    pub async fn kill_tree(mut self) -> Result<()> {
        #[cfg(unix)]
        {
            let pgid = self.pid as i32;
            unsafe { libc::kill(-pgid, libc::SIGTERM) };
            let grace = tokio::time::timeout(Duration::from_secs(3), self.child.wait()).await;
            if grace.is_err() {
                unsafe { libc::kill(-pgid, libc::SIGKILL) };
                let _ = self.child.wait().await;
            }
            Ok(())
        }
        #[cfg(not(unix))]
        {
            self.child.kill().await.context("kill failed")?;
            Ok(())
        }
    }

    /// Wait for natural exit with a deadline; returns the exit code, or
    /// kills the tree and errors when the deadline passes.
    pub async fn wait_with_deadline(mut self, deadline: Duration) -> Result<i32> {
        match tokio::time::timeout(deadline, self.child.wait()).await {
            Ok(status) => Ok(status?.code().unwrap_or(-1)),
            Err(_) => {
                self.kill_tree().await.ok();
                anyhow::bail!("process exceeded its deadline of {}s", deadline.as_secs())
            }
        }
    }
}

/// A dropped supervisor must not leave a server behind: `kill_on_drop` only
/// reaches the shell we spawned, so the whole group goes down here. This is
/// what makes cancelling a campaign safe - the future is dropped, and the
/// app under test dies with it.
#[cfg(unix)]
impl Drop for SupervisedProcess {
    fn drop(&mut self) {
        if let Ok(None) = self.child.try_wait() {
            unsafe { libc::kill(-(self.pid as i32), libc::SIGKILL) };
        }
    }
}

fn shell_command(command_line: &str) -> std::process::Command {
    #[cfg(unix)]
    {
        let mut cmd = std::process::Command::new("sh");
        cmd.arg("-c").arg(command_line);
        cmd
    }
    #[cfg(not(unix))]
    {
        let mut cmd = std::process::Command::new("cmd");
        cmd.arg("/C").arg(command_line);
        cmd
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[tokio::test]
    async fn kill_tree_stops_a_long_running_group() {
        let tmp = tempfile::tempdir().unwrap();
        let log = tmp.path().join("out.log");
        let proc =
            SupervisedProcess::spawn("sleep 300", tmp.path(), &log, None).unwrap();
        let pid = proc.pid;
        proc.kill_tree().await.unwrap();
        // The group leader must be gone (kill(0) probes for existence).
        #[cfg(unix)]
        {
            let alive = unsafe { libc::kill(pid as i32, 0) } == 0;
            assert!(!alive, "process {pid} still alive after kill_tree");
        }
    }

    /// Cancelling a campaign drops the supervisor without awaiting it; the
    /// grandchildren (npm -> node -> workers) must go down all the same.
    #[cfg(unix)]
    #[tokio::test]
    async fn dropping_the_supervisor_kills_the_whole_group() {
        let tmp = tempfile::tempdir().unwrap();
        let log = tmp.path().join("out.log");
        let pid_file = tmp.path().join("child.pid");
        let command = format!("sleep 300 & echo $! > {} ; wait", pid_file.display());
        let proc = SupervisedProcess::spawn(&command, tmp.path(), &log, None).unwrap();

        let mut grandchild = 0;
        for _ in 0..50 {
            if let Ok(text) = std::fs::read_to_string(&pid_file) {
                if let Ok(pid) = text.trim().parse::<i32>() {
                    grandchild = pid;
                    break;
                }
            }
            tokio::time::sleep(Duration::from_millis(100)).await;
        }
        assert!(grandchild > 0, "the child never reported its pid");

        drop(proc);
        tokio::time::sleep(Duration::from_millis(300)).await;
        let alive = unsafe { libc::kill(grandchild, 0) } == 0;
        assert!(!alive, "grandchild {grandchild} survived the drop");
    }

    #[tokio::test]
    async fn logs_are_captured() {
        let tmp = tempfile::tempdir().unwrap();
        let log = tmp.path().join("out.log");
        let proc =
            SupervisedProcess::spawn("echo hello-shadow", tmp.path(), &log, None).unwrap();
        let code = proc.wait_with_deadline(Duration::from_secs(10)).await.unwrap();
        assert_eq!(code, 0);
        let content = std::fs::read_to_string(&log).unwrap();
        assert!(content.contains("hello-shadow"));
    }

    #[tokio::test]
    async fn deadline_kills_and_errors() {
        let tmp = tempfile::tempdir().unwrap();
        let log = tmp.path().join("out.log");
        let proc = SupervisedProcess::spawn("sleep 300", tmp.path(), &log, None).unwrap();
        let result = proc.wait_with_deadline(Duration::from_millis(200)).await;
        assert!(result.is_err());
    }
}
