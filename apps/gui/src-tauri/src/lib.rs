use std::{
    io::{Read, Write},
    net::{TcpListener, TcpStream},
    process::{Child, Command, Stdio},
    sync::Mutex,
    time::{Duration, Instant},
};

use tauri::{
    image::Image,
    menu::{Menu, MenuItem, PredefinedMenuItem},
    tray::{MouseButton, MouseButtonState, TrayIconBuilder, TrayIconEvent},
    App, AppHandle, Manager, WebviewWindow,
};

const GUI_HOST: &str = "127.0.0.1";
const DEFAULT_GUI_PORT: u16 = 9120;
const MIN_SPLASH_MS: u64 = 0;
const SPLASH_URL: &str = "data:text/html,%3C!doctype%20html%3E%3Cmeta%20charset%3Dutf-8%3E%3Cstyle%3Ebody%7Bmargin%3A0%3Bheight%3A100vh%3Bdisplay%3Agrid%3Bplace-items%3Acenter%3Bbackground%3A%23071313%3Bcolor%3A%23f0e6d2%3Bfont%3A14px%20monospace%3Bletter-spacing%3A.08em%3Btext-transform%3Auppercase%7D%3C%2Fstyle%3E%3Cbody%3EStarting%20Hermes%E2%80%A6%3C%2Fbody%3E";

struct GuiState {
    child: Mutex<Option<Child>>,
    port: Mutex<u16>,
}

fn gui_url(port: u16) -> String {
    format!("http://{GUI_HOST}:{port}")
}

fn check_health(port: u16) -> bool {
    let Ok(mut stream) = TcpStream::connect_timeout(
        &format!("{GUI_HOST}:{port}").parse().unwrap(),
        Duration::from_secs(1),
    ) else {
        return false;
    };

    let _ = stream.set_read_timeout(Some(Duration::from_secs(1)));
    let request =
        format!("GET /api/health HTTP/1.1\r\nHost: {GUI_HOST}:{port}\r\nConnection: close\r\n\r\n");

    if stream.write_all(request.as_bytes()).is_err() {
        return false;
    }

    let mut response = String::new();
    let _ = stream.read_to_string(&mut response);
    response.contains("200 OK")
        && response.contains("\"status\":\"ok\"")
        && response.contains("\"mode\":\"gui\"")
}

fn can_bind(port: u16) -> bool {
    TcpListener::bind((GUI_HOST, port)).is_ok()
}

fn base_port() -> u16 {
    std::env::var("HERMES_GUI_PORT")
        .ok()
        .and_then(|raw| raw.parse().ok())
        .unwrap_or(DEFAULT_GUI_PORT)
}

fn select_port() -> u16 {
    let start = base_port();
    for port in start..start.saturating_add(20) {
        if check_health(port) || can_bind(port) {
            return port;
        }
    }
    start
}

fn repo_root() -> std::path::PathBuf {
    std::path::PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        .join("../../..")
        .canonicalize()
        .unwrap_or_else(|_| std::path::PathBuf::from("."))
}

fn runtime_dir() -> Option<std::path::PathBuf> {
    std::env::var_os("HERMES_GUI_RUNTIME_DIR").map(std::path::PathBuf::from)
}

fn runtime_python(runtime: &std::path::Path) -> std::path::PathBuf {
    if cfg!(target_os = "windows") {
        runtime.join("venv").join("Scripts").join("python.exe")
    } else {
        runtime.join("venv").join("bin").join("python")
    }
}

fn wsl_path(root: &std::path::Path) -> Option<(String, String)> {
    let raw = root.to_string_lossy().replace('\\', "/");
    let parts: Vec<&str> = raw.split('/').collect();
    let host = parts.get(2)?.to_ascii_lowercase();
    if host != "wsl$" && host != "wsl.localhost" {
        return None;
    }
    let distro = parts.get(3)?.to_string();
    let path = format!("/{}", parts.get(4..)?.join("/"));
    Some((distro, path))
}

fn start_dashboard(port: u16) -> std::io::Result<Child> {
    if let Some(runtime) = runtime_dir() {
        let python = runtime_python(&runtime);
        let web_dist = runtime.join("web_dist");
        let tui_dir = runtime.join("ui-tui");
        let port = port.to_string();
        return Command::new(python)
            .args([
                "-m",
                "hermes_cli.main",
                "dashboard",
                "--gui",
                "--no-open",
                "--host",
                GUI_HOST,
                "--port",
                &port,
            ])
            .env("HERMES_GUI", "1")
            .env("HERMES_GUI_PORT", &port)
            .env("HERMES_WEB_DIST", web_dist)
            .env("HERMES_TUI_DIR", tui_dir)
            .envs(
                std::env::vars()
                    .filter(|(key, _)| matches!(key.as_str(), "HERMES_HOME" | "HERMES_GUI_FRESH")),
            )
            .stdin(Stdio::null())
            .stdout(Stdio::null())
            .stderr(Stdio::null())
            .spawn();
    }

    let root = repo_root();
    let port = port.to_string();

    if let Some((distro, path)) = wsl_path(&root) {
        let port_env = format!("HERMES_GUI_PORT={port}");
        let mut env_args = vec!["HERMES_GUI=1".to_string(), port_env];
        if let Ok(home) = std::env::var("HERMES_HOME") {
            env_args.push(format!("HERMES_HOME={home}"));
        }
        if let Ok(fresh) = std::env::var("HERMES_GUI_FRESH") {
            env_args.push(format!("HERMES_GUI_FRESH={fresh}"));
        }
        let mut args = vec![
            "-d".to_string(),
            distro,
            "--cd".to_string(),
            path,
            "env".to_string(),
        ];
        args.extend(env_args);
        args.extend([
            "python".to_string(),
            "-m".to_string(),
            "hermes_cli.main".to_string(),
            "dashboard".to_string(),
            "--gui".to_string(),
            "--no-open".to_string(),
            "--host".to_string(),
            GUI_HOST.to_string(),
            "--port".to_string(),
            port.clone(),
        ]);
        return Command::new("wsl.exe")
            .args(args)
            .stdin(Stdio::null())
            .stdout(Stdio::null())
            .stderr(Stdio::null())
            .spawn();
    }

    Command::new("python")
        .args([
            "-m",
            "hermes_cli.main",
            "dashboard",
            "--gui",
            "--no-open",
            "--host",
            GUI_HOST,
            "--port",
            &port,
        ])
        .current_dir(root)
        .env("HERMES_GUI", "1")
        .env("HERMES_GUI_PORT", &port)
        .envs(
            std::env::vars()
                .filter(|(key, _)| matches!(key.as_str(), "HERMES_HOME" | "HERMES_GUI_FRESH")),
        )
        .stdin(Stdio::null())
        .stdout(Stdio::null())
        .stderr(Stdio::null())
        .spawn()
}

fn stop_owned_dashboard(state: &GuiState) {
    let Some(mut child) = state.child.lock().expect("gui child lock poisoned").take() else {
        return;
    };
    let _ = child.kill();
    let _ = child.wait();
}

fn current_port(state: &GuiState) -> u16 {
    *state.port.lock().expect("gui port lock poisoned")
}

fn ensure_dashboard(state: &GuiState) -> Result<(), String> {
    let current = current_port(state);
    if check_health(current) {
        return Ok(());
    }

    let port = select_port();
    *state.port.lock().expect("gui port lock poisoned") = port;

    if check_health(port) {
        return Ok(());
    }

    let child = start_dashboard(port).map_err(|err| {
        format!(
            "Could not auto-start Hermes dashboard ({err}). Start it manually with: hermes dashboard --gui --no-open --port {port}"
        )
    })?;
    *state.child.lock().expect("gui child lock poisoned") = Some(child);
    Ok(())
}

fn navigate_when_ready(window: WebviewWindow, port: u16) {
    std::thread::spawn(move || {
        let started = Instant::now();
        while started.elapsed() < Duration::from_secs(60) {
            if check_health(port) {
                let min_splash = std::env::var("HERMES_GUI_MIN_SPLASH_MS")
                    .ok()
                    .and_then(|raw| raw.parse::<u64>().ok())
                    .unwrap_or(MIN_SPLASH_MS);
                let elapsed = started.elapsed();
                if elapsed < Duration::from_millis(min_splash) {
                    std::thread::sleep(Duration::from_millis(min_splash) - elapsed);
                }
                if let Ok(url) = tauri::Url::parse(&gui_url(port)) {
                    let _ = window.navigate(url);
                    let _ = window.show();
                    let _ = window.set_focus();
                }
                return;
            }
            std::thread::sleep(Duration::from_millis(500));
        }
    });
}

fn show_main_window(app: &AppHandle) {
    if let Some(window) = app.get_webview_window("main") {
        let _ = window.show();
        let _ = window.set_focus();
    }
}

fn open_browser(port: u16) {
    let url = gui_url(port);

    #[cfg(target_os = "windows")]
    let _ = Command::new("cmd")
        .args(["/C", "start", "", &url])
        .stdin(Stdio::null())
        .stdout(Stdio::null())
        .stderr(Stdio::null())
        .spawn();

    #[cfg(target_os = "macos")]
    let _ = Command::new("open").arg(&url).spawn();

    #[cfg(all(unix, not(target_os = "macos")))]
    let _ = Command::new("xdg-open").arg(&url).spawn();
}

fn tray_icon() -> Image<'static> {
    let width = 32;
    let height = 32;
    let mut rgba = Vec::with_capacity(width * height * 4);

    for y in 0..height {
        for x in 0..width {
            let mark = (14..=17).contains(&x) && (5..=26).contains(&y)
                || (8..=23).contains(&x) && (13..=16).contains(&y)
                || (10..=21).contains(&x) && (y == 5 || y == 26);
            if mark {
                rgba.extend_from_slice(&[0xF0, 0xE6, 0xD2, 0xFF]);
            } else {
                rgba.extend_from_slice(&[0x07, 0x13, 0x13, 0xFF]);
            }
        }
    }

    Image::new_owned(rgba, width as u32, height as u32)
}

fn restart_runtime(app: &AppHandle) -> Result<(), String> {
    let state = app.state::<GuiState>();
    stop_owned_dashboard(&state);
    ensure_dashboard(&state)?;

    if let Some(window) = app.get_webview_window("main") {
        if let Ok(url) = tauri::Url::parse(SPLASH_URL) {
            let _ = window.navigate(url);
        }
        let port = current_port(&state);
        navigate_when_ready(window, port);
    }

    Ok(())
}

fn setup_tray(app: &App) -> tauri::Result<()> {
    let open_item = MenuItem::with_id(app, "open", "Open Hermes", true, None::<&str>)?;
    let browser_item = MenuItem::with_id(app, "browser", "Open in Browser", true, None::<&str>)?;
    let restart_item =
        MenuItem::with_id(app, "restart", "Restart Hermes Runtime", true, None::<&str>)?;
    let status_item = MenuItem::with_id(app, "status", "Local runtime", false, None::<&str>)?;
    let separator = PredefinedMenuItem::separator(app)?;
    let separator2 = PredefinedMenuItem::separator(app)?;
    let quit_item = MenuItem::with_id(app, "quit", "Quit Hermes", true, None::<&str>)?;

    let menu = Menu::with_items(
        app,
        &[
            &open_item,
            &browser_item,
            &restart_item,
            &separator,
            &status_item,
            &separator2,
            &quit_item,
        ],
    )?;

    let icon = tray_icon();
    let _tray = TrayIconBuilder::new()
        .icon(icon)
        .menu(&menu)
        .tooltip("Hermes")
        .on_menu_event(|app, event| match event.id.as_ref() {
            "open" => show_main_window(app),
            "browser" => {
                let state = app.state::<GuiState>();
                open_browser(current_port(&state));
            }
            "restart" => {
                if let Err(err) = restart_runtime(app) {
                    eprintln!("Failed to restart Hermes runtime: {err}");
                }
            }
            "quit" => {
                let state = app.state::<GuiState>();
                stop_owned_dashboard(&state);
                app.exit(0);
            }
            _ => {}
        })
        .on_tray_icon_event(|tray, event| {
            if let TrayIconEvent::Click {
                button: MouseButton::Left,
                button_state: MouseButtonState::Up,
                ..
            } = event
            {
                show_main_window(&tray.app_handle());
            }
        })
        .build(app)?;

    Ok(())
}

#[tauri::command]
fn runtime_running(app: AppHandle) -> bool {
    let state = app.state::<GuiState>();
    check_health(current_port(&state))
}

#[tauri::command]
fn restart_runtime_command(app: AppHandle) -> Result<(), String> {
    restart_runtime(&app)
}

pub fn run() {
    tauri::Builder::default()
        .plugin(tauri_plugin_notification::init())
        .plugin(tauri_plugin_opener::init())
        .manage(GuiState {
            child: Mutex::new(None),
            port: Mutex::new(base_port()),
        })
        .invoke_handler(tauri::generate_handler![
            runtime_running,
            restart_runtime_command
        ])
        .setup(|app| {
            setup_tray(app)?;

            if let Some(window) = app.get_webview_window("main") {
                if let Ok(url) = tauri::Url::parse(SPLASH_URL) {
                    let _ = window.navigate(url);
                }

                let state = app.state::<GuiState>();
                if let Err(err) = ensure_dashboard(&state) {
                    eprintln!("{err}");
                }

                let port = current_port(&state);
                navigate_when_ready(window, port);
            }
            Ok(())
        })
        .on_window_event(|window, event| {
            if let tauri::WindowEvent::CloseRequested { api, .. } = event {
                api.prevent_close();
                let _ = window.hide();
            }
        })
        .run(tauri::generate_context!())
        .expect("failed to run Hermes GUI");
}
