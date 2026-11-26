use std::{env, path::PathBuf, process::Command};

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
    let status = Command::new("go")
        .args(["build", "-mod=mod", "-tags=ziren", "-trimpath", "-ldflags=-s -w"])
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
