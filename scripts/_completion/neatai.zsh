#compdef NeatAi NeatAi-backup NeatAi-calendar NeatAi-contacts NeatAi-cookbook NeatAi-docs NeatAi-gallery NeatAi-mail NeatAi-mcp NeatAi-memory NeatAi-notes NeatAi-personal NeatAi-preset NeatAi-research NeatAi-sessions NeatAi-signature NeatAi-skills NeatAi-tasks NeatAi-theme NeatAi-webhook
# Zsh tab-completion for the NeatAi umbrella + sub-CLIs.
#
# Drop in any directory on $fpath, e.g.:
#     fpath=(/path/to/NeatAi-ui/scripts/_completion $fpath)
#     autoload -U compinit; compinit
#
# Then `NeatAi <tab>` completes subcommands; `NeatAi mail <tab>`
# completes mail subcommands; `NeatAi-mail <tab>` works the same.

_NeatAi_scripts_dir() {
    local self="${(%):-%x}"
    while [[ -L "$self" ]]; do self="$(readlink "$self")"; done
    cd "${self:h}/.." && pwd
}

typeset -gA _NeatAi_subs

_NeatAi_refresh() {
    _NeatAi_subs=()
    local dir="$(_NeatAi_scripts_dir)"
    local py="$dir/../venv/bin/python"
    [[ -x "$py" ]] || py="$(command -v python3)"
    local f sub help_out commands
    for f in "$dir"/NeatAi-*; do
        [[ -x "$f" ]] || continue
        case "$f" in
            *.bak|*.pyc|*.pre-*) continue ;;
        esac
        sub="${${f:t}#NeatAi-}"
        help_out=$("$py" "$f" --help 2>/dev/null) || continue
        commands=$(echo "$help_out" | grep -oE '\{[a-z0-9_,-]+\}' | head -1 \
            | tr -d '{}' | tr ',' ' ')
        _NeatAi_subs[$sub]="$commands"
    done
}

_NeatAi() {
    [[ ${#_NeatAi_subs} -eq 0 ]] && _NeatAi_refresh

    local cmd="${words[1]}"

    if [[ "$cmd" == "NeatAi" ]]; then
        if (( CURRENT == 2 )); then
            local -a subs=(${(k)_NeatAi_subs} help)
            _describe 'subcommand' subs
            return
        fi
        local sub="${words[2]}"
        if [[ "$sub" == "help" ]] && (( CURRENT == 3 )); then
            local -a subs=(${(k)_NeatAi_subs})
            _describe 'subcommand' subs
            return
        fi
        if (( CURRENT == 3 )); then
            local -a sc=(${(s/ /)_NeatAi_subs[$sub]})
            _describe 'command' sc
            return
        fi
        return
    fi

    # NeatAi-foo <tab>
    local sub="${cmd#NeatAi-}"
    if (( CURRENT == 2 )); then
        local -a sc=(${(s/ /)_NeatAi_subs[$sub]})
        _describe 'command' sc
        return
    fi
}

_NeatAi "$@"
