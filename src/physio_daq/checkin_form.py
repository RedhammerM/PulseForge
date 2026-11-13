"""Qt dialogs for the session check-in. Logic and validation live in checkin.py."""

from datetime import datetime

from .checkin import BP_READINGS, RATINGS, make_bp, make_checkin, session_day_slot


def _dialog(title: str, ok_text: str, parent=None):
    from PyQt6 import QtWidgets as W

    dlg = W.QDialog(parent)
    dlg.setWindowTitle(title)
    form = W.QFormLayout()
    outer = W.QVBoxLayout(dlg)
    outer.addLayout(form)
    row = W.QHBoxLayout()
    ok, cancel = W.QPushButton(ok_text), W.QPushButton("Cancel")
    ok.setDefault(True)
    ok.clicked.connect(dlg.accept)
    cancel.clicked.connect(dlg.reject)
    row.addStretch(1)
    row.addWidget(cancel)
    row.addWidget(ok)
    outer.addLayout(row)
    return W, dlg, form


def _spin(W, lo: int, hi: int, value: int | None = None):
    sp = W.QSpinBox()
    sp.setRange(lo - 1, hi)  # the minimum is displayed as "n/a"
    sp.setSpecialValueText("n/a")
    sp.setValue(lo - 1 if value is None else value)
    return sp


def _value(sp) -> int | None:
    return None if sp.value() == sp.minimum() else sp.value()


def ask_checkin(subject: str, parent=None) -> dict | None:
    from PyQt6 import QtCore

    now = datetime.now()
    day, slot = session_day_slot(now)
    W, dlg, form = _dialog("Session check-in (before the cuff)", "Save", parent)
    subject_edit = W.QLineEdit(subject)
    day_edit = W.QDateEdit(QtCore.QDate.fromString(day, "yyyy-MM-dd"))
    day_edit.setDisplayFormat("yyyy-MM-dd")
    day_edit.setCalendarPopup(True)
    slot_box = W.QComboBox()
    slot_box.addItems(["AM", "PM"])
    slot_box.setCurrentText(slot)
    waking = _spin(W, 0, 720)
    ratings = {k: _spin(W, 0, 10) for k in RATINGS}
    form.addRow("Subject ID (pseudonym)", subject_edit)
    form.addRow("Wake-up date", day_edit)
    form.addRow("Slot", slot_box)
    form.addRow("Minutes since waking", waking)
    for key, label in RATINGS.items():
        form.addRow(label, ratings[key])
    if not dlg.exec():
        return None
    return make_checkin(
        subject_edit.text().strip(),
        day_edit.date().toString("yyyy-MM-dd"),
        slot_box.currentText(),
        {k: _value(sp) for k, sp in ratings.items()},
        _value(waking),
        now,
    )


def ask_bp(parent=None) -> dict | None:
    """Opens when the recording ends. A reading left on n/a is not stored."""
    W, dlg, form = _dialog("Cuff BP readings of this session", "Save", parent)
    rows = {k: (_spin(W, 60, 260), _spin(W, 30, 160), _spin(W, 30, 200)) for k in BP_READINGS}
    arm = W.QComboBox()
    arm.addItems(["left", "right"])
    titles = {"before_seated": "Before, seated", "during_stand": "During stand"}
    for key, (sbp, dbp, hr) in rows.items():
        line = W.QHBoxLayout()
        for label, sp in (("SBP", sbp), ("DBP", dbp), ("HR", hr)):
            line.addWidget(W.QLabel(label))
            line.addWidget(sp)
        form.addRow(titles[key], line)
    form.addRow("Arm", arm)
    while dlg.exec():
        readings = {
            k: (_value(s), _value(d), _value(h))
            for k, (s, d, h) in rows.items()
            if _value(s) is not None and _value(d) is not None
        }
        if not readings:
            return None
        try:
            return make_bp(readings, arm.currentText(), datetime.now())
        except ValueError as e:
            W.QMessageBox.warning(dlg, "Check the BP values", str(e))
    return None
