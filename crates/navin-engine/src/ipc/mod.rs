//! Local IPC between the daemon and its clients (Navin Desktop, CLI).
//!
//! Transport: a TCP listener on `127.0.0.1:0`, line-delimited JSON, one
//! implementation for Linux, macOS and Windows.
//!
//! It used to be a Unix domain socket, which left Windows with no daemon at
//! all. Named pipes would have been the idiomatic replacement there, but they
//! would have meant two transports to keep in step, and a Python client that
//! can only reach them through Win32 handles with no usable read timeout. A
//! loopback port is one code path, is trivially reachable from every language
//! in this repo, and - unlike a socket file on a 9P share - is also how a
//! Windows gateway reaches a daemon running inside a WSL distribution.
//!
//! What the socket gave for free was access control. That is now explicit:
//! the daemon publishes an ephemeral port and a random token in
//! [`endpoint`], and [`session`] refuses every connection that cannot quote
//! the token back.

pub mod endpoint;
pub mod events;
pub mod protocol;
pub mod server;
pub mod session;
