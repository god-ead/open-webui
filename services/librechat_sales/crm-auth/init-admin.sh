#!/bin/sh
set -eu

script_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
admin_email=${LIBRECHAT_ADMIN_EMAIL:-}

if [ -z "$admin_email" ]; then
  if [ ! -t 0 ]; then
    echo "LIBRECHAT_ADMIN_EMAIL is required in non-interactive mode" >&2
    exit 1
  fi
  printf "LibreChat admin email: "
  IFS= read -r admin_email
fi

if [ -z "$admin_email" ]; then
  echo "LibreChat admin email cannot be empty" >&2
  exit 1
fi

set +e
LIBRECHAT_ADMIN_EMAIL=$admin_email node "$script_dir/init-admin.js" --check
check_status=$?
set -e

case "$check_status" in
  0)
    ;;
  10)
    admin_password=${LIBRECHAT_ADMIN_PASSWORD:-}
    if [ -z "$admin_password" ]; then
      if [ ! -t 0 ]; then
        echo "LIBRECHAT_ADMIN_PASSWORD is required to create a new admin in non-interactive mode" >&2
        exit 1
      fi

      printf "LibreChat admin password: "
      stty -echo
      trap 'stty echo; printf "\n"' EXIT HUP INT TERM
      IFS= read -r admin_password
      printf "\nConfirm password: "
      IFS= read -r confirm_password
      stty echo
      trap - EXIT HUP INT TERM
      printf "\n"

      if [ "$admin_password" != "$confirm_password" ]; then
        echo "LibreChat admin passwords do not match" >&2
        exit 1
      fi
    fi

    if [ -z "$admin_password" ]; then
      echo "LibreChat admin password cannot be empty" >&2
      exit 1
    fi
    export LIBRECHAT_ADMIN_PASSWORD=$admin_password
    ;;
  *)
    exit "$check_status"
    ;;
esac

export LIBRECHAT_ADMIN_EMAIL=$admin_email
node "$script_dir/init-admin.js"
