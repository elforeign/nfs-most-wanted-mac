nfs_raise_open_files() {
    soft=$(ulimit -Sn); hard=$(ulimit -Hn); target=4096
    case $hard in unlimited) ;; *) [ "$hard" -ge "$target" ] || target=$hard ;; esac
    case $soft in unlimited) ;; *)
        if [ "$soft" -lt "$target" ]; then ulimit -Sn "$target" || return 1; fi ;;
    esac
    echo "${1:-runtime}: open files soft $soft -> $(ulimit -Sn), hard $hard"
}
