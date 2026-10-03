import calendar


def add_months(dt, months):
    """Same day-of-month `months` later, clamped to the month's last day (Jan 31 + 1 = Feb 28/29)."""
    month_index = dt.month - 1 + months
    year, month = dt.year + month_index // 12, month_index % 12 + 1
    day = min(dt.day, calendar.monthrange(year, month)[1])
    return dt.replace(year=year, month=month, day=day)
