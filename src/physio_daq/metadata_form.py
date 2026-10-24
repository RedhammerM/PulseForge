"""Small Qt form shown when Start is pressed. Values end up in the JSON."""

TESTS = [
    "supine rest",
    "paced breathing 0.1 Hz",
    "spontaneous breathing",
    "active stand",
    "Valsalva",
    "cold pressor",
    "post-exercise recovery",
    "other",
]
WHEN = ["morning", "evening", "pre-workout", "post-workout", "other"]
POSTURE = ["supine", "sitting", "standing"]
CAFFEINE = ["n/a", "no", "yes"]


def ask_metadata(current: dict) -> dict | None:
    """Show the form prefilled with `current`; return the new values or None.

    Uses whichever Qt binding matplotlib found. Numeric fields left on "n/a"
    are returned as None.
    """
    from matplotlib.backends.qt_compat import QtWidgets as W

    dlg = W.QDialog()
    dlg.setWindowTitle("New recording - metadata")
    form = W.QFormLayout()

    def combo(items: list[str], key: str, editable: bool = False):
        c = W.QComboBox()
        c.addItems(items)
        c.setEditable(editable)
        v = current.get(key)
        if v:
            i = c.findText(v)
            if i >= 0:
                c.setCurrentIndex(i)
            elif editable:
                c.setCurrentText(v)
        return c

    def spin(hi: float, key: str, step: float = 1.0, decimals: int = 0):
        sp = W.QDoubleSpinBox()
        sp.setDecimals(decimals)
        sp.setRange(-1, hi)  # the minimum (-1) is displayed as "n/a"
        sp.setSingleStep(step)
        sp.setSpecialValueText("n/a")
        v = current.get(key)
        sp.setValue(-1 if v is None else v)
        return sp

    def number(sp):
        v = sp.value()
        if v < 0:
            return None
        return int(v) if sp.decimals() == 0 else round(v, 2)

    subject = W.QLineEdit(current.get("subject", ""))
    test = combo(TESTS, "test", editable=True)
    when = combo(WHEN, "when")
    posture = combo(POSTURE, "posture")
    rpe = spin(10, "last_session_rpe")
    workout = spin(600, "last_workout_min")
    sleep = spin(16, "sleep_h", 0.5, 1)
    caffeine = combo(CAFFEINE, "caffeine_today")
    meal = spin(24, "hours_since_meal", 0.5, 1)
    notes = W.QLineEdit(current.get("notes", ""))

    form.addRow("Subject ID (pseudonym)", subject)
    form.addRow("Test / protocol", test)
    form.addRow("When", when)
    form.addRow("Posture", posture)
    form.addRow("RPE of last session (0-10)", rpe)
    form.addRow("Last workout duration [min]", workout)
    form.addRow("Sleep last night [h]", sleep)
    form.addRow("Caffeine today", caffeine)
    form.addRow("Hours since last meal", meal)
    form.addRow("Notes", notes)

    outer = W.QVBoxLayout(dlg)
    outer.addLayout(form)
    row = W.QHBoxLayout()
    ok, cancel = W.QPushButton("Start recording"), W.QPushButton("Cancel")
    ok.setDefault(True)
    ok.clicked.connect(dlg.accept)
    cancel.clicked.connect(dlg.reject)
    row.addStretch(1)
    row.addWidget(cancel)
    row.addWidget(ok)
    outer.addLayout(row)

    if not dlg.exec():
        return None
    return {
        "subject": subject.text().strip(),
        "test": test.currentText().strip(),
        "when": when.currentText(),
        "posture": posture.currentText(),
        "last_session_rpe": number(rpe),
        "last_workout_min": number(workout),
        "sleep_h": number(sleep),
        "caffeine_today": None if caffeine.currentText() == "n/a" else caffeine.currentText(),
        "hours_since_meal": number(meal),
        "notes": notes.text().strip(),
    }
