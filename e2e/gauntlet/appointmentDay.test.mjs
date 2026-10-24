import assert from 'node:assert/strict';
import test from 'node:test';

import { clickEnabledAppointmentDay } from './appointmentDay.mjs';

const MONTHS = [
  'January',
  'February',
  'March',
  'April',
  'May',
  'June',
  'July',
  'August',
  'September',
  'October',
  'November',
  'December',
];

// Mirrors CalendarGrid in frontend/src/pages/Appointments.jsx: the open month
// disables past days, Sundays, and days more than 30 days ahead.
function calendarPage(today, { nextEnabled = true } = {}) {
  const start = new Date(today.getFullYear(), today.getMonth(), today.getDate());
  const maxDate = new Date(start);
  maxDate.setDate(maxDate.getDate() + 30);
  let viewYear = start.getFullYear();
  let viewMonth = start.getMonth();
  const actions = [];

  function enabledDayNumbers() {
    const daysInMonth = new Date(viewYear, viewMonth + 1, 0).getDate();
    const days = [];
    for (let day = 1; day <= daysInMonth; day += 1) {
      const date = new Date(viewYear, viewMonth, day);
      date.setHours(0, 0, 0, 0);
      const isPast = date < start;
      const isBeyond = date > maxDate;
      const isSunday = date.getDay() === 0;
      if (!isPast && !isBeyond && !isSunday) days.push(day);
    }
    return days;
  }

  function canGoNext() {
    if (!nextEnabled) return false;
    return new Date(viewYear, viewMonth + 1, 1) <= maxDate;
  }

  function advance() {
    if (viewMonth === 11) {
      viewMonth = 0;
      viewYear += 1;
    } else {
      viewMonth += 1;
    }
  }

  return {
    actions,
    heading() {
      return `${MONTHS[viewMonth]} ${viewYear}`;
    },
    enabledDayNumbers,
    getByRole(_role, options = {}) {
      const name = options.name;
      const dayQuery = name instanceof RegExp;
      return {
        async count() {
          if (!dayQuery) return name === 'Next month' ? 1 : 0;
          if (options.disabled === false) return enabledDayNumbers().length;
          return new Date(viewYear, viewMonth + 1, 0).getDate();
        },
        async isEnabled() {
          if (name === 'Next month') return canGoNext();
          return enabledDayNumbers().length > 0;
        },
        async click() {
          if (name !== 'Next month') throw new Error(`unexpected click ${String(name)}`);
          if (!canGoNext()) throw new Error('Next month is disabled');
          advance();
          actions.push('next-month');
        },
        first() {
          return {
            async waitFor() {},
            async click() {
              const days = enabledDayNumbers();
              if (days.length === 0) {
                throw new Error('no enabled day');
              }
              actions.push(`day:${days[0]}`);
            },
          };
        },
      };
    },
  };
}

test('a month ending on Sunday has no enabled day until Next month', async () => {
  for (const [year, month, day, nextHeading, nextDay] of [
    [2027, 0, 31, 'February 2027', 1],
    [2028, 11, 31, 'January 2029', 1],
    [2026, 4, 31, 'June 2026', 1],
  ]) {
    const today = new Date(year, month, day, 12);
    assert.equal(today.getDay(), 0, `${today.toDateString()} is a Sunday`);
    assert.equal(new Date(year, month + 1, 0).getDate(), day);

    const page = calendarPage(today);
    const enabled = page.getByRole('button', { name: /^\d{1,2}$/, disabled: false });
    assert.equal(await enabled.count(), 0);
    await assert.rejects(() => enabled.first().click(), /no enabled day/);

    await clickEnabledAppointmentDay(page);
    assert.deepEqual(page.actions, ['next-month', `day:${nextDay}`]);
    assert.equal(page.heading(), nextHeading);
  }
});

test('a Sunday that is not the last day stays in the current month', async () => {
  const today = new Date(2026, 9, 25, 12);
  assert.equal(today.getDay(), 0);
  const page = calendarPage(today);
  assert.deepEqual(page.enabledDayNumbers().slice(0, 1), [26]);

  await clickEnabledAppointmentDay(page);
  assert.deepEqual(page.actions, ['day:26']);
  assert.equal(page.heading(), 'October 2026');
});

test('a mid-month weekday is clicked without leaving the month', async () => {
  const today = new Date(2026, 9, 23, 12);
  assert.equal(today.getDay(), 5);
  const page = calendarPage(today);

  await clickEnabledAppointmentDay(page);
  assert.deepEqual(page.actions, ['day:23']);
  assert.equal(page.heading(), 'October 2026');
});

test('a month with no enabled day and no Next month fails instead of waiting', async () => {
  const page = calendarPage(new Date(2027, 0, 31, 12), { nextEnabled: false });
  await assert.rejects(
    () => clickEnabledAppointmentDay(page),
    /Next month is unavailable/,
  );
  assert.deepEqual(page.actions, []);
});
