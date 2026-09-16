# Source workbooks

The two audit workbooks are **not committed**: they hold local patient IDs,
ages and attendance times for identifiable patients.

Put them here before running anything:

    data/
      Audit_CIWA.xlsx                   sheet "Feuille 1"
      AE_PresentingComplaint_39_2.xlsx  sheet "Sheet3"

The loader matches them by filename pattern (`*Audit_CIWA*.xlsx` and
`*PresentingComplaint*.xlsx`), so the date-stamped names the trust exports work
as they are. To keep them somewhere else, set one of:

    ALCOHOL_AUDIT_DATA_DIR        directory holding both workbooks
    ALCOHOL_AUDIT_CIWA_FILE       full path to the CIWA audit workbook
    ALCOHOL_AUDIT_COMPLAINT_FILE  full path to the presenting-complaint workbook

Only `Sheet3` of the presenting-complaint workbook is read. The other sheets in
that file cover a different period and are deliberately ignored.
