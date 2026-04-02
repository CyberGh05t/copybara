# opentele patches

Three patches required for Telegram Desktop 6.7.0+ compatibility.

After installation, find files in `venv/lib/python3.*/site-packages/opentele/td/`.

---

## Patch 1 — account.py: unknown key type 0x17

**Problem:** TD 6.7.0 added new key types (0x17, 0x18, 0x19). opentele only knows up to 0x16 and raises an exception.

Find:
```python
raise OpenTeleException(f"Unknown key type {lskType}")
```

Replace with:
```python
break  # skip unknown key types
```

---

## Patch 2 — account.py: infinite recursion in `api` setter

**Problem:** `Account.api = value` calls `TDesktop.api = value` → `Account.api = value` → RecursionError.

Find in the `api` setter:
```python
self.owner.api = value
```

Replace with:
```python
self.owner._TDesktop__api = value
```

---

## Patch 3 — tdesktop.py: silent exception swallowing

**Problem:** errors during account loading are silently ignored.

Find the bare `except: pass` in the account loading loop, replace with:
```python
except Exception as e:
    print(f"[opentele] account load error: {e}")
    pass
```
