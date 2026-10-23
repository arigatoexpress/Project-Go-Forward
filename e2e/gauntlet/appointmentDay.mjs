// CalendarGrid opens on the current month and disables past days, Sundays,
// and dates more than 30 days out. When today is a Sunday and also the last
// day of that month, every date button is disabled. Clicking the first
// enabled day then times out, even though the next month can be booked.

const DAY_BUTTON = /^\d{1,2}$/;

export async function clickEnabledAppointmentDay(page) {
  const enabledDays = () => page.getByRole('button', { name: DAY_BUTTON, disabled: false });
  await page.getByRole('button', { name: DAY_BUTTON }).first().waitFor({
    state: 'visible',
    timeout: 20_000,
  });

  if ((await enabledDays().count()) === 0) {
    const nextMonth = page.getByRole('button', { name: 'Next month' });
    if (!(await nextMonth.isEnabled())) {
      throw new Error(
        'Appointment calendar has no enabled date, and Next month is unavailable.',
      );
    }
    await nextMonth.click();
  }

  // click() waits for the enabled day, including the one that appears after
  // Next month replaces the grid.
  await enabledDays().first().click();
}
