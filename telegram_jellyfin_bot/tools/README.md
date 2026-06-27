# Bundled Local Bot API runtime

This folder contains a Windows x64 build of the official
[Telegram Bot API server](https://github.com/tdlib/telegram-bot-api) and its
runtime dependencies from [OpenSSL](https://www.openssl.org/) and
[zlib](https://zlib.net/).

The binaries are included for Windows portability. When replacing them, use
trusted upstream builds, update all matching runtime DLLs together, and record
the new versions and hashes.

## SHA-256

```text
telegram-bot-api.exe  0C0EE0397F7BF21D4A0A175D555356C888C29FFA5624DFAACAD4BFB56770E5EB
legacy.dll            EA546367ADD4C6FEE6AE2BF7EE9B84D374AFD2939A72B0F7315958F42BF852D9
libcrypto-3-x64.dll   A605DDA2FBB72E48A4AB7014350E61DAA67FA9B5762D975C23FDD11C9EF6D363
libssl-3-x64.dll      41A297C96A7128F6317404E8574015F457C2C184FF82495B19BD0E95BD533B80
z.dll                 8E69CDEECFC3D9857539886919691A25678B4D9941B45AE67AA552F7E724BFA9
```

Redistributors are responsible for retaining the license notices supplied by
the upstream projects/build environment.
