"""Descriptor-based service I/O in tenant-writable directories."""
from contextlib import contextmanager
import os
from pathlib import Path
import secrets
import stat

import paths


@contextmanager
def home_directory(home, *, create=False, owner=None, owner_root=None):
    # Only the operator-controlled base may have canonical aliases (e.g. /var
    # on macOS). Never resolve tenant-controlled components or follow their links.
    base = Path(paths.accounts_dir()).parent.absolute()
    home = Path(home).absolute()
    spike = os.environ.get("MDAGENT_SPIKE_DSH_HOME")
    if spike and os.environ.get("MDAGENT_SKIP_UNIX") == "1" and home == Path(spike).absolute():
        base = home.parent
    parts = home.relative_to(base).parts
    if not parts or any(part in {".", ".."} for part in parts):
        raise ValueError("unsafe tenant home")
    owned = Path(owner_root or home).absolute().relative_to(base).parts
    if not owned or any(part in {".", ".."} for part in owned) or parts[:len(owned)] != owned:
        raise ValueError("unsafe tenant ownership root")
    fd = os.open(base.resolve(strict=True), os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        info = os.fstat(fd)
        if info.st_uid not in {0, os.geteuid()} or info.st_mode & 0o022:
            raise ValueError("tenant base must be service-owned and not group/world writable")
        for index, part in enumerate(parts):
            created = False
            if create:
                try:
                    os.mkdir(part, 0o700, dir_fd=fd)
                    created = True
                except FileExistsError:
                    pass
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            if created and owner is not None and index + 1 >= len(owned):
                try:
                    os.fchown(child, *owner)
                except BaseException:
                    os.close(child)
                    raise
            os.close(fd)
            fd = child
        yield fd
    finally:
        os.close(fd)


def _regular(info):
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
        raise ValueError("tenant service file must be a regular file with one link")


def write_private(home, name, body, owner=None, *, owner_root=None):
    """Publish an exclusive private temporary file; never chown a pathname."""
    with home_directory(home, create=True, owner=owner, owner_root=owner_root) as directory:
        try:
            _regular(os.stat(name, dir_fd=directory, follow_symlinks=False))
        except FileNotFoundError:
            pass
        temporary = ".mdagent-" + secrets.token_hex(16)
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                     0o600, dir_fd=directory)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                stream.write(body)
                stream.flush()
                if owner is not None:
                    os.fchown(stream.fileno(), *owner)
                os.fsync(stream.fileno())
            os.replace(temporary, name, src_dir_fd=directory, dst_dir_fd=directory)
        finally:
            try:
                os.unlink(temporary, dir_fd=directory)
            except FileNotFoundError:
                pass
    return str(Path(home) / name)


def open_log(home):
    with home_directory(home, create=True) as directory:
        fd = os.open("web.log", os.O_RDWR | os.O_APPEND | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK,
                     0o600, dir_fd=directory)
        try:
            info = os.fstat(fd)
            _regular(info)
            if info.st_uid != os.geteuid():
                raise ValueError("tenant log must be service-owned")
            os.fchmod(fd, 0o600)
            stream = os.fdopen(fd, "a+", encoding="utf-8")
        except BaseException:
            os.close(fd)
            raise
        return stream


def read_private(home, name):
    with home_directory(home) as directory:
        fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
        with os.fdopen(fd, "r", encoding="utf-8") as stream:
            _regular(os.fstat(stream.fileno()))
            data = stream.read(65537)
            if len(data) > 65536:
                raise ValueError("tenant credential file too large")
            return data


def unlink_private(home, name):
    with home_directory(home) as directory:
        try:
            os.unlink(name, dir_fd=directory)
        except FileNotFoundError:
            pass
