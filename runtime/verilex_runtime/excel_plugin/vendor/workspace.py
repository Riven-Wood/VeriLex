"""Minimal workbook name adapter for upstream cell operations."""

def sheet_names(sheet):
    return sheet.parent.sheetnames
