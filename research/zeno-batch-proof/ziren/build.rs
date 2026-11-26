use std::{env, fs, path::PathBuf, process::Command};


fn main() {
    let guest = PathBuf::from(env::var_os("ZENO_GUEST_DIR").expect("missing guest directory"));
    let output = PathBuf::from(env::var_os("OUT_DIR").expect("missing output directory"));
    let go_env = [
        ("GOOS", "linux"),
        ("GOARCH", "mipsle"),
        ("GOMIPS", "softfloat"),
        ("CGO_ENABLED", "0"),
        ("GOTOOLCHAIN", "local"),
        ("GOWORK", "off"),
    ];
    let overlay = zkm_build::generate_go_overlay_for(&output, &guest, &go_env)
        .expect("missing Ziren Go runtime overlay");
    let goroot = Command::new("go")
        .args(["env", "GOROOT"])
        .current_dir(&guest)
        .envs(go_env)
        .output()
        .expect("locate pinned Go toolchain");
    assert!(goroot.status.success(), "go env GOROOT failed");
    let goroot = String::from_utf8(goroot.stdout).expect("GOROOT must be UTF-8");
    // Use the ordinary Linux errors for the guest's absent paths and TTYs.
    // Other syscalls still go through Ziren's normal decoder and AIR.
    let syscall_source =
        PathBuf::from(goroot.trim()).join("src/internal/runtime/syscall/asm_linux_mipsx.s");
    let syscall_overlay = output.join("asm_linux_mipsx.s");
    let syscall = fs::read_to_string(&syscall_source).expect("read Go syscall source");
    let entry = "\tSYSCALL\n\tBEQ\tR7, ok\n";
    assert_eq!(syscall.matches(entry).count(), 1, "unexpected MIPS syscall source");
    let isolated = syscall.replace(entry, concat!(
        "\tMOVW\t$4288, R1 // openat\n",
        "\tBEQ\tR1, R2, pathMissing\n",
        "\tMOVW\t$4298, R1 // readlinkat\n",
        "\tBEQ\tR1, R2, pathMissing\n",
        "\tMOVW\t$4054, R1 // ioctl\n",
        "\tBEQ\tR1, R2, noTTY\n",
        "\tSYSCALL\n",
        "\tBEQ\tR7, ok\n",
        "returnError:\n",
    ));
    fs::write(&syscall_overlay, isolated + concat!(
        "pathMissing:\n",
        "\tMOVW\t$2, R2 // ENOENT\n",
        "\tJMP\treturnError\n",
        "noTTY:\n",
        "\tMOVW\t$25, R2 // ENOTTY\n",
        "\tJMP\treturnError\n",
    ))
    .expect("write isolated Go syscall overlay");
    let mut replacements: serde_json::Value =
        serde_json::from_slice(&fs::read(&overlay).expect("read Ziren overlay"))
            .expect("parse Ziren overlay");
    replacements["Replace"][syscall_source.to_str().expect("UTF-8 Go path")] =
        serde_json::json!(syscall_overlay);
    let timer_source = PathBuf::from(goroot.trim()).join("src/runtime/time.go");
    let timer_overlay = output.join("runtime_time.go");
    let timer = fs::read_to_string(&timer_source).expect("read Go timer source");
    let timer_poll = concat!(
        "\t// Timers rely on the network poller, so make sure the poller\n",
        "\t// has started.\n",
        "\tif netpollInited.Load() == 0 {\n",
        "\t\tnetpollGenericInit()\n",
        "\t}\n",
    );
    assert_eq!(timer.matches(timer_poll).count(), 1, "unexpected Go timer source");
    fs::write(&timer_overlay, timer.replace(timer_poll, "\t// The guest has no network poller.\n"))
        .expect("write timer overlay");
    replacements["Replace"][timer_source.to_str().expect("UTF-8 Go path")] =
        serde_json::json!(timer_overlay);

    let poll_source = PathBuf::from(goroot.trim()).join("src/internal/poll/fd_poll_runtime.go");
    let poll_overlay = output.join("fd_poll_runtime.go");
    let poll = fs::read_to_string(&poll_source).expect("read Go poll source");
    let init = "func (pd *pollDesc) init(fd *FD) error {";
    assert_eq!(poll.matches(init).count(), 1, "unexpected Go poll source");
    let (before, body) = poll.split_once(init).unwrap();
    let (_, after) = body.split_once("\n}\n").expect("end of Go poll initialization");
    fs::write(&poll_overlay, format!("{before}{init}\n\treturn syscall.ENOSYS\n}}\n{after}"))
        .expect("write guest poll overlay");
    replacements["Replace"][poll_source.to_str().expect("UTF-8 Go path")] =
        serde_json::json!(poll_overlay);
    fs::write(&overlay, serde_json::to_vec(&replacements).expect("encode Go overlay"))
        .expect("write complete guest runtime overlay");
    let status = Command::new("go")
        // Keep the symbol table for Ziren's Go runtime patches.
        .args([
            "build",
            "-mod=mod",
            "-tags=ziren",
            "-trimpath",
            "-buildvcs=false",
            "-ldflags=-w -buildid=",
        ])
        .arg("-overlay")
        .arg(overlay)
        .arg("-o")
        .arg(output.join("vault-guest"))
        .arg(".")
        .current_dir(&guest)
        .envs(go_env)
        .status()
        .expect("launch MIPS Go build");
    assert!(status.success(), "MIPS Go build failed");
    println!("cargo:rerun-if-env-changed=ZENO_GUEST_DIR");
    println!("cargo:rerun-if-changed={}", guest.display());
}
