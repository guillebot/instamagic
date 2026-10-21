import os
import pwd
import sys


def main() -> None:
    os.makedirs("/data/derivatives", exist_ok=True)
    if os.geteuid() == 0:
        account = pwd.getpwnam("app")
        os.chown("/data/derivatives", account.pw_uid, account.pw_gid)
        os.setgid(account.pw_gid)
        os.setuid(account.pw_uid)
    os.execvp(sys.argv[1], sys.argv[1:])


if __name__ == "__main__":
    main()
