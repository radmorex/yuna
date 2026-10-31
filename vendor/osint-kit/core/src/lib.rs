//! Rust-ядро osint-kit: то, что упирается в процессор.
//! Сеть здесь не трогаем — защита от выхода в локальную сеть живёт в osint_kit/net.py.
//! Каждая функция повторяет Python-фолбэк в osint_kit/core.py; совпадение проверяют тесты.

use std::collections::{BTreeSet, HashMap};
use std::fs::File;
use std::io::Read;
use std::sync::OnceLock;

use pyo3::exceptions::PyOSError;
use pyo3::prelude::*;
use rayon::prelude::*;
use regex::Regex;
use sha2::{Digest, Sha256};

fn hash_path(path: &str) -> std::io::Result<String> {
    let mut fh = File::open(path)?;
    let mut h = Sha256::new();
    let mut buf = vec![0u8; 1 << 20];
    loop {
        let n = fh.read(&mut buf)?;
        if n == 0 {
            break;
        }
        h.update(&buf[..n]);
    }
    Ok(hex::encode(h.finalize()))
}

/// SHA-256 файла; GIL отпущен на время чтения.
#[pyfunction]
fn sha256_file(py: Python<'_>, path: String) -> PyResult<String> {
    py.allow_threads(|| hash_path(&path))
        .map_err(|e| PyOSError::new_err(format!("{path}: {e}")))
}

/// SHA-256 многих файлов параллельно: {путь: hex}. Ошибка любого файла — исключение.
#[pyfunction]
fn sha256_files(py: Python<'_>, paths: Vec<String>) -> PyResult<HashMap<String, String>> {
    py.allow_threads(|| {
        paths
            .par_iter()
            .map(|p| hash_path(p).map(|d| (p.clone(), d)).map_err(|e| format!("{p}: {e}")))
            .collect::<Result<HashMap<_, _>, _>>()
    })
    .map_err(PyOSError::new_err)
}

struct Patterns {
    url: Regex,
    email: Regex,
    ipv4: Regex,
    md5: Regex,
    sha1: Regex,
    sha256: Regex,
}

fn patterns() -> &'static Patterns {
    static P: OnceLock<Patterns> = OnceLock::new();
    P.get_or_init(|| Patterns {
        // Границы слов — ASCII, как re.ASCII в Python: «ї», «é» и диакритики не считаются частью слова.
        url: Regex::new(r#"(?i)https?://[^\s<>"'()\[\]{}]+"#).unwrap(),
        email: Regex::new(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,24}").unwrap(),
        ipv4: Regex::new(r"(?-u:\b)(?:[0-9]{1,3}\.){3}[0-9]{1,3}(?-u:\b)").unwrap(),
        md5: Regex::new(r"(?-u:\b)[A-Fa-f0-9]{32}(?-u:\b)").unwrap(),
        sha1: Regex::new(r"(?-u:\b)[A-Fa-f0-9]{40}(?-u:\b)").unwrap(),
        sha256: Regex::new(r"(?-u:\b)[A-Fa-f0-9]{64}(?-u:\b)").unwrap(),
    })
}

fn valid_ipv4(s: &str) -> bool {
    s.split('.').all(|o| o.parse::<u16>().map_or(false, |v| v <= 255))
}

/// Не часть длинной цепочки через точку (номер версии 1.2.3.4.5, OID).
fn standalone_ipv4(text: &str, start: usize, end: usize) -> bool {
    let rest = &text.as_bytes()[end..];
    let before_dot = start > 0 && text.as_bytes()[start - 1] == b'.';
    let after_dot_digit = rest.len() >= 2 && rest[0] == b'.' && rest[1].is_ascii_digit();
    !before_dot && !after_dot_digit
}

fn collect(re: &Regex, text: &str, f: impl Fn(&str) -> Option<String>) -> Vec<String> {
    re.find_iter(text).filter_map(|m| f(m.as_str())).collect::<BTreeSet<_>>().into_iter().collect()
}

/// Индикаторы из текста: отсортированные уникальные списки по типам.
#[pyfunction]
fn extract_iocs(py: Python<'_>, text: String) -> HashMap<&'static str, Vec<String>> {
    py.allow_threads(|| {
        let p = patterns();
        let hex = |s: &str| Some(s.to_lowercase());
        HashMap::from([
            ("urls", collect(&p.url, &text, |s| {
                let t = s.trim_end_matches(['.', ',', ';', ':', '!', '?']);
                (t.len() > "http://".len()).then(|| t.to_string())
            })),
            ("emails", collect(&p.email, &text, |s| Some(s.to_lowercase()))),
            ("ipv4", p.ipv4.find_iter(&text)
                .filter(|m| valid_ipv4(m.as_str()) && standalone_ipv4(&text, m.start(), m.end()))
                .map(|m| m.as_str().to_string())
                .collect::<BTreeSet<_>>().into_iter().collect()),
            ("md5", collect(&p.md5, &text, hex)),
            ("sha1", collect(&p.sha1, &text, hex)),
            ("sha256", collect(&p.sha256, &text, hex)),
        ])
    })
}

#[pymodule]
fn deflix_core(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(sha256_file, m)?)?;
    m.add_function(wrap_pyfunction!(sha256_files, m)?)?;
    m.add_function(wrap_pyfunction!(extract_iocs, m)?)?;
    Ok(())
}
