import { afterEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import Appointments from '../pages/Appointments';
import { clickEnabledAppointmentDay } from '../../../e2e/gauntlet/appointmentDay.mjs';

function isoDate(year, monthIndex, day) {
  const date = new Date(year, monthIndex, day);
  date.setHours(0, 0, 0, 0);
  return date.toISOString().slice(0, 10);
}

function queryButtons(name, disabled) {
  const found = screen.queryAllByRole('button', { name });
  if (disabled === false) return found.filter((button) => !button.disabled);
  if (disabled === true) return found.filter((button) => button.disabled);
  return found;
}

function enabledDayButtons() {
  return screen.getAllByRole('button', { name: /^\d{1,2}$/ }).filter((button) => !button.disabled);
}

function pageFor(actions) {
  return {
    getByRole(_role, options = {}) {
      const { name, disabled } = options;
      return {
        async count() {
          return queryButtons(name, disabled).length;
        },
        async isEnabled() {
          const [button] = queryButtons(name, disabled);
          return Boolean(button && !button.disabled);
        },
        async click() {
          const [button] = queryButtons(name, disabled);
          if (!button) throw new Error(`No button for ${String(name)}`);
          if (name === 'Next month') actions.push('next-month');
          fireEvent.click(button);
        },
        first() {
          return {
            async waitFor() {
              const [button] = queryButtons(name, disabled);
              if (!button) throw new Error(`No button for ${String(name)}`);
            },
            async click() {
              const [button] = queryButtons(name, disabled);
              if (!button) throw new Error(`No button for ${String(name)}`);
              actions.push(`day:${button.textContent.trim()}`);
              fireEvent.click(button);
            },
          };
        },
      };
    },
  };
}

function renderOn(year, monthIndex, day) {
  vi.useFakeTimers({ toFake: ['Date'] });
  vi.setSystemTime(new Date(year, monthIndex, day, 12, 0, 0));
  global.fetch = vi.fn().mockResolvedValue({
    ok: true,
    json: async () => ({ available_slots: ['10:00 AM'], business_hours: '9-5' }),
  });
  render(<Appointments onBack={() => {}} />);
}

afterEach(() => {
  cleanup();
  vi.useRealTimers();
  vi.restoreAllMocks();
  delete global.fetch;
});

describe('appointment calendar when a month ends on Sunday', () => {
  it.each([
    ['January 2027', 2027, 0, 31, 'February 2027', 1],
    ['December 2028', 2028, 11, 31, 'January 2029', 1],
  ])(
    '%s has no enabled date until Next month',
    (openHeading, year, monthIndex, day, nextHeading, nextDay) => {
      renderOn(year, monthIndex, day);
      const today = new Date(year, monthIndex, day, 12);
      expect(today.getDay()).toBe(0);
      expect(new Date(year, monthIndex + 1, 0).getDate()).toBe(day);

      expect(screen.getByRole('heading', { name: openHeading })).toBeInTheDocument();
      expect(enabledDayButtons()).toHaveLength(0);
      expect(screen.getByRole('button', { name: 'Next month' })).toBeEnabled();

      fireEvent.click(screen.getByRole('button', { name: 'Next month' }));
      expect(screen.getByRole('heading', { name: nextHeading })).toBeInTheDocument();
      expect(screen.getByRole('button', { name: String(nextDay) })).toBeEnabled();
      expect(screen.getByRole('button', { name: '7' })).toBeDisabled();
    },
  );

  it.each([
    ['January 2027', 2027, 0, 31, 2027, 1, 1],
    ['December 2028', 2028, 11, 31, 2029, 0, 1],
  ])(
    'the gauntlet selector books %s by advancing to the next month',
    async (_label, year, monthIndex, day, nextYear, nextMonth, nextDay) => {
      renderOn(year, monthIndex, day);
      expect(enabledDayButtons()).toHaveLength(0);

      const actions = [];
      await clickEnabledAppointmentDay(pageFor(actions));

      expect(actions).toEqual(['next-month', `day:${nextDay}`]);
      expect(global.fetch).toHaveBeenCalledWith(
        `/api/appointments/slots?date=${isoDate(nextYear, nextMonth, nextDay)}`,
      );
    },
  );

  it('keeps a Sunday that is not the last day in the current month', async () => {
    renderOn(2026, 9, 25);
    expect(new Date(2026, 9, 25, 12).getDay()).toBe(0);
    expect(enabledDayButtons().map((button) => button.textContent.trim())[0]).toBe('26');

    const actions = [];
    await clickEnabledAppointmentDay(pageFor(actions));

    expect(actions).toEqual(['day:26']);
    expect(global.fetch).toHaveBeenCalledWith(
      `/api/appointments/slots?date=${isoDate(2026, 9, 26)}`,
    );
  });
});
