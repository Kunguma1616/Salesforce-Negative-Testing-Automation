import os, re, time, json, zipfile, logging, traceback
import pandas as pd
from pathlib import Path
from datetime import datetime

from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException, NoSuchElementException
from webdriver_manager.chrome import ChromeDriverManager
from dotenv import load_dotenv

# =========================
# CONFIG
# =========================
LOGIN_URL = "https://test.salesforce.com/"
HOME_URL  = "https://chumley--staging.sandbox.lightning.force.com/lightning/page/home"

# <<<--- CHANGE THIS TO YOUR CSV FILE --- >>>
CSV_FILE = r"C:\Users\User\Downloads\Fully_corrupted_negative-only_data__sample_20_.csv"

SF_USERNAME = os.getenv("SF_USERNAME", "kunguma.balaji@aspect.co.uk.staging")
SF_PASSWORD = os.getenv("SF_PASSWORD") or input("Enter Salesforce password: ")

# =========================
# ENV + LOGGING
# =========================
try:
    env_path = Path(__file__).parent / ".env"
    if env_path.exists():
        load_dotenv(env_path)
except Exception:
    pass

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    handlers=[logging.StreamHandler(), logging.FileHandler("neg_fill_then_validate.log", encoding="utf-8")]
)

# =========================
# Reporter (screenshots + JSON + zip)
# =========================
class Reporter:
    def __init__(self, driver, base="NegFillValidate"):
        self.driver = driver
        ts = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        self.run_name = f"{base}_{ts}"
        self.outdir = Path("artifacts") / self.run_name
        self.outdir.mkdir(parents=True, exist_ok=True)
        self.json_path = self.outdir / "report.json"
        self.log_path  = self.outdir / "run.log"
        self.steps = []
        self.counter = 0

        fh = logging.FileHandler(self.log_path, encoding="utf-8")
        fh.setFormatter(logging.Formatter("%(asctime)s | %(levelname)s | %(message)s"))
        logging.getLogger().addHandler(fh)

    def _snap(self, name):
        self.counter += 1
        safe = re.sub(r"[^\w\-.]+", "_", name.strip().lower())
        fn = f"{self.counter:03d}_{safe}.png"
        path = self.outdir / fn
        try:
            self.driver.save_screenshot(str(path))
            return fn
        except Exception as e:
            logging.error(f"Screenshot failed: {e}")
            return None

    def info(self, step, msg):
        shot = self._snap(step)
        logging.info(f"{step} | {msg}")
        self.steps.append({"t": datetime.now().isoformat(), "level": "INFO", "step": step, "msg": msg, "screenshot": shot})

    def error(self, step, msg, ex=None):
        shot = self._snap(step)
        logging.error(f"{step} | {msg}")
        if ex:
            logging.error(traceback.format_exc())
        self.steps.append({"t": datetime.now().isoformat(), "level": "ERROR", "step": step, "msg": msg, "screenshot": shot, "exception": str(ex) if ex else None})

    def finalize(self):
        with open(self.json_path, "w", encoding="utf-8") as f:
            json.dump(self.steps, f, indent=2)
        zip_path = self.outdir.parent / f"{self.run_name}_artifacts.zip"
        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as z:
            for p in self.outdir.glob("*"):
                z.write(p, arcname=p.name)
        logging.info(f"Artifacts zipped: {zip_path}")

# =========================
# WebDriver
# =========================
def init_driver():
    opts = Options()
    opts.add_argument("--start-maximized")
    opts.add_argument("--disable-blink-features=AutomationControlled")
    opts.add_argument("--no-sandbox")
    opts.add_argument("--disable-dev-shm-usage")
    opts.add_experimental_option("excludeSwitches", ["enable-logging"])
    opts.add_experimental_option("detach", True)
    service = Service(ChromeDriverManager().install())
    return webdriver.Chrome(service=service, options=opts)

# =========================
# Login + Navigate to Form
# =========================
def login(driver, rep):
    try:
        driver.get(LOGIN_URL)
        rep.info("Open_Login", LOGIN_URL)
        wait = WebDriverWait(driver, 30)
        u = wait.until(EC.presence_of_element_located((By.ID, "username")))
        p = driver.find_element(By.ID, "password")
        b = driver.find_element(By.ID, "Login")
        u.clear(); u.send_keys(SF_USERNAME)
        p.clear(); p.send_keys(SF_PASSWORD)
        rep.info("Credentials_Entered", SF_USERNAME)
        b.click()
        rep.info("Click_Login", "Clicked")

        # optional MFA wait
        try:
            WebDriverWait(driver, 10).until(
                EC.presence_of_element_located((By.XPATH, "//*[contains(., 'Verify Your Identity')]"))
            )
            rep.info("MFA", "Waiting for manual verification (40s)")
            time.sleep(40)
        except TimeoutException:
            pass

        wait.until(lambda d: "lightning" in d.current_url or "setup" in d.current_url)
        time.sleep(2)
        rep.info("Login_Success", driver.current_url)
        return True
    except Exception as e:
        rep.error("Login_Failed", "Could not login", e)
        return False

def open_form(driver, rep):
    try:
        driver.get(HOME_URL)
        rep.info("Open_Home", HOME_URL)
        time.sleep(3)

        # Choose "Create Domestic Customer"
        domestic = driver.find_elements(By.XPATH, "//*[contains(., 'Create Domestic Customer')]")
        clicked = False
        for el in domestic:
            try:
                radio = el.find_element(By.XPATH, ".//ancestor::*[.//input[@type='radio']][1]//input[@type='radio']")
                driver.execute_script("arguments[0].scrollIntoView({block:'center'});", radio)
                driver.execute_script("arguments[0].click();", radio)
                rep.info("Choose_Domestic", "Selected radio")
                clicked = True
                break
            except Exception:
                continue
        if not clicked:
            rep.error("Domestic_Radio_NotFound", "Could not click Domestic radio")
            return False

        # Click Next near it
        next_btn = None
        try:
            next_btn = driver.find_element(By.XPATH, "//button[contains(@class,'slds-button_brand')][contains(.,'Next')]")
        except NoSuchElementException:
            btns = driver.find_elements(By.XPATH, "//button[contains(.,'Next')]")
            if btns: next_btn = btns[0]
        if not next_btn:
            rep.error("Next_NotFound", "No Next button found")
            return False

        driver.execute_script("arguments[0].scrollIntoView({block:'center'});", next_btn)
        driver.execute_script("arguments[0].click();", next_btn)
        rep.info("Click_Next", "Navigating to form…")
        time.sleep(4)

        # sanity check
        inputs = driver.find_elements(By.XPATH, "//input[@type='text' or @type='email' or @type='tel']")
        if len(inputs) < 3:
            rep.error("Form_NotLoaded", "Too few inputs")
            return False

        rep.info("Form_Loaded", f"Inputs: {len(inputs)}")
        return True
    except Exception as e:
        rep.error("Open_Form_Failed", "Exception opening form", e)
        return False

# =========================
# Field helpers
# =========================
def find_input(driver, labels_or_hints):
    # Flexible finders; tweak if your labels vary
    patterns = [
        "//label[contains(.,'{q}')]/following::input[1]",
        "//input[contains(@placeholder,'{q}')]",
        "//input[contains(@name,'{q}')]",
        "//input[@type='text' and contains(@aria-label,'{q}')]",
        "//input[@type='email' and contains(@aria-label,'{q}')]",
        "//input[@type='tel' and contains(@aria-label,'{q}')]",
    ]
    for q in labels_or_hints:
        for xp in patterns:
            try:
                el = driver.find_element(By.XPATH, xp.format(q=q))
                return el
            except Exception:
                continue
    return None

def clear_and_type(driver, el, text):
    try:
        el.click()
    except Exception:
        driver.execute_script("arguments[0].scrollIntoView({block:'center'});", el)
        driver.execute_script("arguments[0].click();", el)
    try:
        el.send_keys(Keys.CONTROL, "a")
    except Exception:
        try:
            el.send_keys(Keys.COMMAND, "a")
        except Exception:
            pass
    el.send_keys(Keys.DELETE)
    el.send_keys(text)

def click_submit(driver):
    btn = None
    for xp in [
        "//button[contains(@class,'slds-button_brand') and (contains(.,'Next') or contains(.,'Submit') or contains(.,'Save') or contains(.,'Create'))]",
        "//button[contains(.,'Next') or contains(.,'Submit') or contains(.,'Save') or contains(.,'Create')]"
    ]:
        try:
            btn = driver.find_element(By.XPATH, xp)
            break
        except Exception:
            continue
    if not btn:
        return False
    driver.execute_script("arguments[0].scrollIntoView({block:'center'});", btn)
    driver.execute_script("arguments[0].click();", btn)
    return True

def wait_for_validation_error(driver, timeout=6):
    """
    Waits briefly for typical SF validation errors after submit.
    Returns (found_bool, error_text_sample)
    """
    # Common patterns:
    # - elements with slds-has-error
    # - aria-invalid="true"
    # - help text spans under fields
    # - generic 'Complete this field' messages
    candidates = [
        "//*[@aria-invalid='true']",
        "//*[contains(@class,'slds-has-error')]",
        "//*[contains(., 'Complete this field')]",
        "//*[contains(., 'required') and contains(@class,'slds-form-element__help')]",
        "//*[contains(@class,'slds-form-element__help')]",
        "//*[contains(@class,'error') and (contains(.,'required') or contains(.,'invalid'))]"
    ]
    end = time.time() + timeout
    while time.time() < end:
        for xp in candidates:
            els = driver.find_elements(By.XPATH, xp)
            if els:
                # grab a little text if any
                text_bits = []
                for e in els[:5]:
                    t = (e.text or "").strip()
                    if t:
                        text_bits.append(t)
                sample = "\n".join(text_bits) if text_bits else "(error elements found)"
                return True, sample
        time.sleep(0.25)
    return False, ""

# =========================
# Fill one row (fill->submit->screenshot->stop)
# =========================
def process_row(driver, rep, row, idx1):
    rep.info(f"Row_{idx1}_Start", "Start row")

    fn = str(row.get("FirstName","") or "")
    ln = str(row.get("LastName","") or "")
    ph = str(row.get("Phone","") or "")
    em = str(row.get("Email","") or "")
    bn = str(row.get("BuildingNumber","") or "")
    addr1 = str(row.get("AddressLine1","") or "")
    city = str(row.get("City","") or "")
    pc = str(row.get("Postcode","") or "")

    # fill all fields (even if wrong)
    pairs = [
        (["First Name","First"], fn, "FirstName"),
        (["Last Name","Last","Surname"], ln, "LastName"),
        (["Phone","Telephone","Mobile"], ph, "Phone"),
        (["Email","E-mail"], em, "Email"),
        (["Building","House"], bn, "BuildingNumber"),
        (["Street","Address Line 1","Address"], addr1, "Address1"),
        (["City","Town"], city, "City"),
        (["Postcode","Postal Code","ZIP"], pc, "Postcode"),
    ]

    for labels, value, tag in pairs:
        el = find_input(driver, labels)
        if el:
            clear_and_type(driver, el, value)
            rep.info(f"Filled_{tag}", f"{tag}='{value}'")
            # optional: brief pause to let inline validators react
            time.sleep(0.2)
        else:
            rep.info(f"Skip_{tag}", f"Input not found; value='{value}'")

    # submit to surface server/client validation
    if not click_submit(driver):
        rep.error("Submit_NotFound", "Submit/Next button not found")
        # Stop this row immediately
        raise RuntimeError("Submit button not found")

    rep.info("Clicked_Submit", "Waiting for validation result")
    time.sleep(2)

    # wait for validation error presence
    found, errtxt = wait_for_validation_error(driver, timeout=6)
    if found:
        # this is what you asked: take screenshot of error state & stop immediately
        rep.error("Validation_Error", f"Detected validation errors:\n{errtxt}")
        raise RuntimeError("Validation error detected")
    else:
        # if no obvious inline error, still capture a post-submit screenshot
        rep.info("No_Inline_Error", "No inline errors detected after submit")

# =========================
# MAIN
# =========================
def main():
    driver = None
    rep = None
    try:
        if not Path(CSV_FILE).exists():
            raise FileNotFoundError(f"CSV not found: {CSV_FILE}")
        df = pd.read_csv(CSV_FILE)
        logging.info(f"Loaded {len(df)} rows from {CSV_FILE}")

        driver = init_driver()
        rep = Reporter(driver)

        if not login(driver, rep):
            return
        if not open_form(driver, rep):
            return

        for i, row in df.iterrows():
            idx1 = i + 1
            rep.info("Row_Start", f"Row {idx1}/{len(df)}")

            # re-open the form fresh for each row so the error state is clean
            if not open_form(driver, rep):
                rep.error("Form_Reload_Failed", f"Row {idx1}: Could not reload form")
                break

            try:
                process_row(driver, rep, row, idx1)
                # if it didn't error (rare for negative data), still record completion
                rep.info(f"Row_{idx1}_Done", "No errors detected — unusual for negative data")

            except RuntimeError as e:
                # expected stop path: screenshot already taken in rep.error
                rep.error(f"Row_{idx1}_Stopped", str(e))
                # continue to next row
                continue
            except Exception as e:
                # unexpected exception
                rep.error(f"Row_{idx1}_Exception", "Unexpected exception", e)
                continue

        rep.info("Run_Complete", "Finished all rows")

    except Exception as e:
        if rep:
            rep.error("Critical", "Fatal error", e)
        else:
            logging.error(f"Fatal: {e}")
            logging.error(traceback.format_exc())
    finally:
        if rep: rep.finalize()
        if driver:
            try: driver.quit()
            except Exception: pass

if __name__ == "__main__":
    main()
