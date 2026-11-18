#!/bin/sh
# The mount's group can traverse but cannot create/replace entries.
set -eu
umask 077
base=/run/rag-ingress
stage=$base/.stage
owner=101:11017
test "$(id -u):$(id -g)" = 101:101
test ! -L /run && test "$(stat -c '%u:%g:%a' /run)" = 0:0:755
test ! -L "$base" && test "$(stat -c '%u:%g:%a' "$base")" = "$owner:2710"
# This must be the dedicated rprivate, writable ext4 mount, not a rootfs directory.
awk -v p="$base" '$5==p { for (i=7;i<=NF;i++) {
    if ($i=="shared:" || $i ~ /^(shared|master|propagate_from):/) exit 1
    if ($i=="-" && $(i+1)=="ext4" && $6 ~ /^rw(,|$)/) n++
    if ($i=="-") break
} } END { if (n!=1) exit 1 }' /proc/self/mountinfo
test -z "$(ls -A "$base")"
mkdir -m 2700 "$stage"
test "$(stat -c '%u:%g:%a' "$stage")" = "$owner:2700"
child=
identity=
cleanup() {
    trap - EXIT TERM INT QUIT
    if [ -n "$child" ]; then
        kill -QUIT "$child" 2>/dev/null || :
        wait "$child" 2>/dev/null || :
    fi
    for sock in "$base/http.sock" "$stage/http.sock"; do
        if [ -n "$identity" ] && [ -S "$sock" ] && [ ! -L "$sock" ] &&
            [ "$(stat -c '%d:%i:%u:%g' "$sock")" = "$identity" ]; then
            rm -- "$sock"
        fi
    done
    rmdir "$stage" 2>/dev/null || :
}
trap cleanup EXIT
trap 'exit 0' TERM INT QUIT
nginx -g 'daemon off;' &
child=$!
i=0
while [ ! -S "$stage/http.sock" ]; do
    kill -0 "$child"
    i=$((i+1))
    [ "$i" -le 40 ] || exit 1
    sleep 0.1
done
test ! -L "$stage/http.sock"
test "$(stat -c '%u:%g:%h' "$stage/http.sock")" = "$owner:1"
identity=$(stat -c '%d:%i:%u:%g' "$stage/http.sock")
chmod 0660 "$stage/http.sock"
test ! -e "$base/http.sock" && test ! -L "$base/http.sock"
# No replace: link into the group-traversable directory only after chmod.
ln -T "$stage/http.sock" "$base/http.sock"
rm -- "$stage/http.sock"
set +e
wait "$child"
status=$?
child=
exit "$status"
