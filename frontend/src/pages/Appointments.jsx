import React, { useState, useEffect } from 'react';
import { Calendar, Clock, User, CheckCircle, ArrowLeft, ArrowRight, MapPin, Phone, Loader2, Download } from 'lucide-react';
import { BUSINESS_NAME, BUSINESS_PHONE, BUSINESS_ADDRESS, BUSINESS_CITY, BUSINESS_STATE } from '../constants';

const BUSINESS_FULL_ADDRESS = `${BUSINESS_ADDRESS}, ${BUSINESS_CITY}, ${BUSINESS_STATE}`;

// Shared focus / field styles to keep keyboard users informed of focus state.
const FIELD_BASE = 'w-full px-4 py-2.5 border rounded-lg outline-none transition focus-visible:ring-2 focus-visible:ring-blue-500 focus-visible:border-blue-500';
const BUTTON_FOCUS = 'focus:outline-none focus-visible:ring-2 focus-visible:ring-offset-2 focus-visible:ring-blue-500';

const DAYS_SHORT = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'];
const DAYS_FULL = ['Sunday', 'Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday'];
const MONTHS = ['January', 'February', 'March', 'April', 'May', 'June',
  'July', 'August', 'September', 'October', 'November', 'December'];

// Step indicator
const StepIndicator = ({ currentStep }) => {
  const steps = [
    { num: 1, label: 'Date', icon: Calendar },
    { num: 2, label: 'Time', icon: Clock },
    { num: 3, label: 'Info', icon: User },
    { num: 4, label: 'Confirm', icon: CheckCircle },
  ];

  return (
    <ol
      className="flex items-center justify-center mb-8"
      aria-label="Booking progress"
    >
      {steps.map((step, i) => {
        const Icon = step.icon;
        const isActive = currentStep === step.num;
        const isComplete = currentStep > step.num;
        const status = isComplete ? 'complete' : isActive ? 'current' : 'upcoming';
        return (
          <React.Fragment key={step.num}>
            <li
              className="flex flex-col items-center"
              aria-current={isActive ? 'step' : undefined}
            >
              <div
                className={`w-10 h-10 rounded-full flex items-center justify-center text-sm font-bold transition-colors
                  ${isComplete ? 'bg-green-600 text-white' : isActive ? 'bg-blue-600 text-white' : 'bg-gray-200 text-gray-600'}`}
                aria-hidden="true"
              >
                {isComplete ? <CheckCircle size={18} /> : <Icon size={18} />}
              </div>
              <span className={`text-xs mt-1 ${isActive ? 'text-blue-700 font-semibold' : 'text-gray-600'}`}>
                {step.label}
              </span>
              <span className="sr-only">{`Step ${step.num} of ${steps.length}: ${step.label} (${status})`}</span>
            </li>
            {i < steps.length - 1 && (
              <div
                className={`w-12 sm:w-20 h-0.5 mx-1 mt-[-12px] ${currentStep > step.num ? 'bg-green-600' : 'bg-gray-200'}`}
                aria-hidden="true"
              />
            )}
          </React.Fragment>
        );
      })}
    </ol>
  );
};

// Calendar grid component
const CalendarGrid = ({ selectedDate, onSelect }) => {
  const today = new Date();
  today.setHours(0, 0, 0, 0);

  const [viewMonth, setViewMonth] = useState(today.getMonth());
  const [viewYear, setViewYear] = useState(today.getFullYear());

  const maxDate = new Date(today);
  maxDate.setDate(maxDate.getDate() + 30);

  const daysInMonth = new Date(viewYear, viewMonth + 1, 0).getDate();
  const firstDayOfWeek = new Date(viewYear, viewMonth, 1).getDay();

  const canGoPrev = viewYear > today.getFullYear() || (viewYear === today.getFullYear() && viewMonth > today.getMonth());
  const canGoNext = new Date(viewYear, viewMonth + 1, 1) <= maxDate;

  const cells = [];
  // Empty cells for days before the 1st
  for (let i = 0; i < firstDayOfWeek; i++) {
    cells.push(<div key={`empty-${i}`} />);
  }

  for (let day = 1; day <= daysInMonth; day++) {
    const d = new Date(viewYear, viewMonth, day);
    d.setHours(0, 0, 0, 0);
    const isPast = d < today;
    const isBeyond = d > maxDate;
    const isToday = d.getTime() === today.getTime();
    const isSelected = selectedDate && d.toISOString().slice(0, 10) === selectedDate;
    const disabled = isPast || isBeyond;

    const dayLabel = `${DAYS_FULL[d.getDay()]}, ${MONTHS[viewMonth]} ${day}, ${viewYear}`;

    cells.push(
      <button
        key={day}
        type="button"
        disabled={disabled}
        onClick={() => onSelect(d.toISOString().slice(0, 10))}
        aria-label={dayLabel}
        aria-pressed={isSelected ? 'true' : 'false'}
        aria-current={isToday ? 'date' : undefined}
        className={`
          aspect-square rounded-lg text-sm font-medium transition-all ${BUTTON_FOCUS}
          ${disabled ? 'text-gray-400 cursor-not-allowed' : 'hover:bg-blue-100 cursor-pointer'}
          ${isSelected ? 'bg-blue-600 text-white hover:bg-blue-700' : ''}
          ${isToday && !isSelected ? 'ring-2 ring-blue-500 text-blue-700 font-bold' : ''}
          ${!disabled && !isSelected && !isToday ? 'text-gray-800' : ''}
        `}
      >
        {day}
      </button>
    );
  }

  return (
    <section
      className="bg-white rounded-xl shadow-md border border-gray-100 p-6"
      aria-labelledby="calendar-heading"
    >
      <div className="flex items-center justify-between mb-4">
        <button
          type="button"
          onClick={() => {
            if (viewMonth === 0) { setViewMonth(11); setViewYear(viewYear - 1); }
            else setViewMonth(viewMonth - 1);
          }}
          disabled={!canGoPrev}
          aria-label="Previous month"
          className={`p-2 rounded-full hover:bg-gray-100 disabled:opacity-30 disabled:cursor-not-allowed transition ${BUTTON_FOCUS}`}
        >
          <ArrowLeft size={20} aria-hidden="true" />
        </button>
        <h2 id="calendar-heading" className="text-lg font-bold text-gray-900" aria-live="polite">
          {MONTHS[viewMonth]} {viewYear}
        </h2>
        <button
          type="button"
          onClick={() => {
            if (viewMonth === 11) { setViewMonth(0); setViewYear(viewYear + 1); }
            else setViewMonth(viewMonth + 1);
          }}
          disabled={!canGoNext}
          aria-label="Next month"
          className={`p-2 rounded-full hover:bg-gray-100 disabled:opacity-30 disabled:cursor-not-allowed transition ${BUTTON_FOCUS}`}
        >
          <ArrowRight size={20} aria-hidden="true" />
        </button>
      </div>
      <div className="grid grid-cols-7 gap-1 mb-2" role="presentation">
        {DAYS_SHORT.map((d, i) => (
          <div
            key={d}
            className="text-center text-xs font-semibold text-gray-600 py-1"
            aria-label={DAYS_FULL[i]}
          >
            {d}
          </div>
        ))}
      </div>
      <div className="grid grid-cols-7 gap-1" role="grid" aria-label={`${MONTHS[viewMonth]} ${viewYear}`}>
        {cells}
      </div>
    </section>
  );
};

// Time slot picker
const TimeSlotPicker = ({ date, onSelect, onBack }) => {
  const [slots, setSlots] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');

  useEffect(() => {
    setLoading(true);
    setError('');
    fetch(`/api/appointments/slots?date=${date}`)
      .then(res => res.json())
      .then(data => {
        if (data.error) {
          setError(data.error);
        } else {
          setSlots(data);
        }
      })
      .catch(() => setError('Unable to load available times. Please try again.'))
      .finally(() => setLoading(false));
  }, [date]);

  const dateObj = new Date(date + 'T12:00:00');
  const dateLabel = dateObj.toLocaleDateString('en-US', { weekday: 'long', month: 'long', day: 'numeric', year: 'numeric' });

  if (loading) {
    return (
      <div className="text-center py-12" role="status" aria-live="polite">
        <Loader2 className="h-8 w-8 animate-spin text-blue-600 mx-auto mb-3" aria-hidden="true" />
        <p className="text-gray-600">Loading available times...</p>
      </div>
    );
  }

  if (error) {
    return (
      <div className="text-center py-12" role="alert">
        <p className="text-red-700 mb-4">{error}</p>
        <button
          type="button"
          onClick={onBack}
          className={`text-blue-700 hover:underline text-sm rounded ${BUTTON_FOCUS}`}
        >
          Choose a different date
        </button>
      </div>
    );
  }

  return (
    <section
      className="bg-white rounded-xl shadow-md border border-gray-100 p-6"
      aria-labelledby="time-picker-heading"
    >
      <div className="flex items-center justify-between mb-4">
        <button
          type="button"
          onClick={onBack}
          className={`text-sm text-blue-700 hover:underline flex items-center rounded ${BUTTON_FOCUS}`}
        >
          <ArrowLeft size={14} className="mr-1" aria-hidden="true" /> Change date
        </button>
        <span className="text-sm text-gray-600">{slots.business_hours}</span>
      </div>
      <h2 id="time-picker-heading" className="text-lg font-bold text-gray-900 mb-1">{dateLabel}</h2>
      <p className="text-sm text-gray-600 mb-4">
        Select a time for your visit <span className="text-gray-500">(Central Time)</span>
      </p>

      {slots.available_slots.length === 0 ? (
        <div className="text-center py-8">
          <p className="text-gray-600 mb-2">No available times for this date.</p>
          <button
            type="button"
            onClick={onBack}
            className={`text-blue-700 hover:underline text-sm rounded ${BUTTON_FOCUS}`}
          >
            Choose a different date
          </button>
        </div>
      ) : (
        <div
          className="grid grid-cols-2 sm:grid-cols-3 gap-3"
          role="radiogroup"
          aria-label="Available appointment times"
        >
          {slots.available_slots.map(slot => (
            <button
              key={slot}
              type="button"
              onClick={() => onSelect(slot)}
              aria-label={`Book at ${slot}`}
              className={`px-4 py-3 border border-gray-300 rounded-lg text-sm font-medium text-gray-800 hover:border-blue-600 hover:bg-blue-50 hover:text-blue-800 transition ${BUTTON_FOCUS}`}
            >
              <Clock size={14} className="inline mr-2 text-gray-500" aria-hidden="true" />
              {slot}
            </button>
          ))}
        </div>
      )}
    </section>
  );
};

// Contact info form
const ContactForm = ({ formData, onChange, onSubmit, onBack, submitting }) => {
  const phoneInvalid = formData.phone && formData.phone.replace(/\D/g, '').length < 10;
  return (
    <section
      className="bg-white rounded-xl shadow-md border border-gray-100 p-6"
      aria-labelledby="appt-contact-heading"
    >
      <button
        type="button"
        onClick={onBack}
        className={`text-sm text-blue-700 hover:underline flex items-center mb-4 rounded ${BUTTON_FOCUS}`}
      >
        <ArrowLeft size={14} className="mr-1" aria-hidden="true" /> Change time
      </button>
      <h2 id="appt-contact-heading" className="text-lg font-bold text-gray-900 mb-4">Your Information</h2>
      <form onSubmit={onSubmit} className="space-y-4" noValidate>
        <div>
          <label htmlFor="appt-name" className="block text-sm font-medium text-gray-700 mb-1">
            Full Name <span className="text-red-600" aria-hidden="true">*</span>
          </label>
          <input
            id="appt-name"
            name="name"
            type="text"
            required
            aria-required="true"
            autoComplete="name"
            value={formData.name}
            onChange={(e) => onChange({ ...formData, name: e.target.value })}
            className={`${FIELD_BASE} border-gray-300`}
            placeholder="John Doe"
          />
        </div>
        <div>
          <label htmlFor="appt-phone" className="block text-sm font-medium text-gray-700 mb-1">
            Phone Number <span className="text-red-600" aria-hidden="true">*</span>
          </label>
          <input
            id="appt-phone"
            name="phone"
            type="tel"
            required
            aria-required="true"
            autoComplete="tel"
            aria-invalid={phoneInvalid ? 'true' : 'false'}
            aria-describedby={phoneInvalid ? 'appt-phone-error' : undefined}
            value={formData.phone}
            onChange={(e) => onChange({ ...formData, phone: e.target.value })}
            className={`${FIELD_BASE} ${phoneInvalid ? 'border-red-400' : 'border-gray-300'}`}
            placeholder="(281) 000-0000"
          />
          {phoneInvalid && (
            <p id="appt-phone-error" className="text-xs text-red-600 mt-1">
              Enter a valid 10-digit phone number
            </p>
          )}
        </div>
        <div>
          <label htmlFor="appt-email" className="block text-sm font-medium text-gray-700 mb-1">
            Email <span className="text-gray-500 font-normal">(optional)</span>
          </label>
          <input
            id="appt-email"
            name="email"
            type="email"
            autoComplete="email"
            value={formData.email}
            onChange={(e) => onChange({ ...formData, email: e.target.value })}
            className={`${FIELD_BASE} border-gray-300`}
            placeholder="john@example.com"
          />
        </div>
        <div>
          <label htmlFor="appt-notes" className="block text-sm font-medium text-gray-700 mb-1">
            What homes are you interested in?
          </label>
          <textarea
            id="appt-notes"
            name="notes"
            rows="3"
            value={formData.notes}
            onChange={(e) => onChange({ ...formData, notes: e.target.value })}
            className={`${FIELD_BASE} border-gray-300`}
            placeholder="e.g., 3 bedroom double wide, budget under $100k..."
          />
        </div>
        <button
          type="submit"
          disabled={submitting || !formData.name.trim() || !formData.phone.trim()}
          className={`w-full bg-blue-700 text-white font-bold py-3 rounded-lg hover:bg-blue-800 transition shadow-md disabled:opacity-50 disabled:cursor-not-allowed flex items-center justify-center ${BUTTON_FOCUS}`}
        >
          {submitting ? (
            <><Loader2 size={18} className="animate-spin mr-2" aria-hidden="true" /> Booking...</>
          ) : (
            'Review & Confirm'
          )}
        </button>
      </form>
    </section>
  );
};

// Generate .ics calendar file content
function generateICS(date, timeSlot, name) {
  const [time, ampm] = timeSlot.split(' ');
  const [hours, minutes] = time.split(':').map(Number);
  let hour24 = hours;
  if (ampm === 'PM' && hours !== 12) hour24 += 12;
  if (ampm === 'AM' && hours === 12) hour24 = 0;

  const startDate = new Date(date + 'T00:00:00');
  startDate.setHours(hour24, minutes, 0);
  const endDate = new Date(startDate);
  endDate.setHours(endDate.getHours() + 1);

  const fmt = (d) => d.toISOString().replace(/[-:]/g, '').replace(/\.\d{3}/, '');

  return [
    'BEGIN:VCALENDAR',
    'VERSION:2.0',
    'PRODID:-//THO//Appointment//EN',
    'BEGIN:VEVENT',
    `DTSTART:${fmt(startDate)}`,
    `DTEND:${fmt(endDate)}`,
    `SUMMARY:Showroom Visit - ${BUSINESS_NAME}`,
    `LOCATION:${BUSINESS_FULL_ADDRESS}`,
    `DESCRIPTION:Appointment for ${name} at ${BUSINESS_NAME}. Call ${BUSINESS_PHONE} with questions.`,
    'END:VEVENT',
    'END:VCALENDAR'
  ].join('\r\n');
}

const Appointments = ({ onBack }) => {
  const [step, setStep] = useState(1);
  const [selectedDate, setSelectedDate] = useState(null);
  const [selectedTime, setSelectedTime] = useState(null);
  const [formData, setFormData] = useState({ name: '', phone: '', email: '', notes: '' });
  const [submitting, setSubmitting] = useState(false);
  const [result, setResult] = useState(null);
  const [error, setError] = useState('');

  const handleDateSelect = (date) => {
    setSelectedDate(date);
    setStep(2);
  };

  const handleTimeSelect = (time) => {
    setSelectedTime(time);
    setStep(3);
  };

  const handleFormSubmit = (e) => {
    e.preventDefault();
    setStep(4);
  };

  const handleConfirm = async () => {
    setSubmitting(true);
    setError('');
    try {
      const resp = await fetch('/api/appointments', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          name: formData.name.trim(),
          phone: formData.phone.trim(),
          email: formData.email.trim() || undefined,
          date: selectedDate,
          time_slot: selectedTime,
          notes: formData.notes.trim() || undefined,
          source: 'website',
        }),
      });
      const data = await resp.json();
      if (data.success) {
        setResult(data);
        setStep(5); // success
      } else {
        setError(data.error || 'Unable to book appointment. Please try again.');
      }
    } catch {
      setError('Connection error. Please try again or call us.');
    } finally {
      setSubmitting(false);
    }
  };

  const handleDownloadICS = () => {
    const ics = generateICS(selectedDate, selectedTime, formData.name);
    const blob = new Blob([ics], { type: 'text/calendar;charset=utf-8' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `tho-appointment-${selectedDate}.ics`;
    a.click();
    URL.revokeObjectURL(url);
  };

  const dateLabel = selectedDate
    ? new Date(selectedDate + 'T12:00:00').toLocaleDateString('en-US', { weekday: 'long', month: 'long', day: 'numeric', year: 'numeric' })
    : '';

  // Success screen
  if (step === 5 && result) {
    return (
      <main
        className="max-w-lg mx-auto px-4 py-12 text-center"
        aria-labelledby="appt-success-heading"
      >
        <div className="bg-white p-8 rounded-2xl shadow-xl border border-gray-100">
          <div className="bg-green-100 p-4 rounded-full inline-block mb-4" aria-hidden="true">
            <CheckCircle className="h-12 w-12 text-green-700" />
          </div>
          <h1 id="appt-success-heading" className="text-2xl font-bold text-gray-900 mb-2">
            Appointment Confirmed!
          </h1>
          <p className="text-gray-700 mb-6">We look forward to seeing you.</p>

          <dl className="bg-gray-50 rounded-xl p-4 text-left space-y-2 mb-6">
            <div className="flex items-center text-sm">
              <Calendar size={16} className="text-blue-700 mr-3 flex-shrink-0" aria-hidden="true" />
              <dt className="sr-only">Date</dt>
              <dd className="font-medium">{dateLabel}</dd>
            </div>
            <div className="flex items-center text-sm">
              <Clock size={16} className="text-blue-700 mr-3 flex-shrink-0" aria-hidden="true" />
              <dt className="sr-only">Time</dt>
              <dd className="font-medium">{selectedTime}</dd>
            </div>
            <div className="flex items-center text-sm">
              <MapPin size={16} className="text-blue-700 mr-3 flex-shrink-0" aria-hidden="true" />
              <dt className="sr-only">Location</dt>
              <dd>{BUSINESS_FULL_ADDRESS}</dd>
            </div>
            <div className="flex items-center text-sm">
              <Phone size={16} className="text-blue-700 mr-3 flex-shrink-0" aria-hidden="true" />
              <dt className="sr-only">Phone</dt>
              <dd>{BUSINESS_PHONE}</dd>
            </div>
          </dl>

          <div className="flex flex-col sm:flex-row gap-3">
            <button
              type="button"
              onClick={handleDownloadICS}
              className={`flex-1 flex items-center justify-center px-4 py-2.5 border border-blue-700 text-blue-700 rounded-lg hover:bg-blue-50 transition font-medium text-sm ${BUTTON_FOCUS}`}
            >
              <Download size={16} className="mr-2" aria-hidden="true" /> Add to Calendar
            </button>
            <button
              type="button"
              onClick={onBack}
              className={`flex-1 bg-blue-700 text-white px-4 py-2.5 rounded-lg hover:bg-blue-800 transition font-medium text-sm ${BUTTON_FOCUS}`}
            >
              Return Home
            </button>
          </div>
        </div>
      </main>
    );
  }

  return (
    <main
      className="max-w-2xl mx-auto px-4 sm:px-6 py-8"
      aria-labelledby="appt-page-heading"
    >
      <header className="mb-6 flex items-center justify-between">
        <div>
          <h1 id="appt-page-heading" className="text-2xl sm:text-3xl font-bold text-blue-900">
            Book an Appointment
          </h1>
          <p className="text-gray-600 text-sm mt-1">Schedule a showroom visit at {BUSINESS_NAME}</p>
        </div>
        <button
          type="button"
          onClick={onBack}
          className={`text-sm font-medium text-blue-700 hover:text-blue-900 rounded ${BUTTON_FOCUS}`}
        >
          &larr; Back
        </button>
      </header>

      <StepIndicator currentStep={step} />

      {/* Step 1: Date */}
      {step === 1 && (
        <CalendarGrid selectedDate={selectedDate} onSelect={handleDateSelect} />
      )}

      {/* Step 2: Time */}
      {step === 2 && (
        <TimeSlotPicker
          date={selectedDate}
          onSelect={handleTimeSelect}
          onBack={() => setStep(1)}
        />
      )}

      {/* Step 3: Contact Info */}
      {step === 3 && (
        <ContactForm
          formData={formData}
          onChange={setFormData}
          onSubmit={handleFormSubmit}
          onBack={() => setStep(2)}
          submitting={false}
        />
      )}

      {/* Step 4: Review & Confirm */}
      {step === 4 && (
        <section
          className="bg-white rounded-xl shadow-md border border-gray-100 p-6"
          aria-labelledby="review-heading"
        >
          <button
            type="button"
            onClick={() => setStep(3)}
            className={`text-sm text-blue-700 hover:underline flex items-center mb-4 rounded ${BUTTON_FOCUS}`}
          >
            <ArrowLeft size={14} className="mr-1" aria-hidden="true" /> Edit info
          </button>
          <h2 id="review-heading" className="text-lg font-bold text-gray-900 mb-4">
            Review Your Appointment
          </h2>

          <dl className="space-y-3 mb-6">
            <div className="flex justify-between py-2 border-b border-gray-100">
              <dt className="text-gray-600 text-sm">Date</dt>
              <dd className="font-medium text-sm">{dateLabel}</dd>
            </div>
            <div className="flex justify-between py-2 border-b border-gray-100">
              <dt className="text-gray-600 text-sm">Time</dt>
              <dd className="font-medium text-sm">{selectedTime}</dd>
            </div>
            <div className="flex justify-between py-2 border-b border-gray-100">
              <dt className="text-gray-600 text-sm">Name</dt>
              <dd className="font-medium text-sm">{formData.name}</dd>
            </div>
            <div className="flex justify-between py-2 border-b border-gray-100">
              <dt className="text-gray-600 text-sm">Phone</dt>
              <dd className="font-medium text-sm">{formData.phone}</dd>
            </div>
            {formData.email && (
              <div className="flex justify-between py-2 border-b border-gray-100">
                <dt className="text-gray-600 text-sm">Email</dt>
                <dd className="font-medium text-sm">{formData.email}</dd>
              </div>
            )}
            {formData.notes && (
              <div className="flex justify-between py-2 border-b border-gray-100">
                <dt className="text-gray-600 text-sm">Notes</dt>
                <dd className="font-medium text-sm text-right max-w-[60%]">{formData.notes}</dd>
              </div>
            )}
            <div className="flex justify-between py-2">
              <dt className="text-gray-600 text-sm">Location</dt>
              <dd className="font-medium text-sm">{BUSINESS_FULL_ADDRESS}</dd>
            </div>
          </dl>

          {error && (
            <p role="alert" className="text-sm text-red-700 bg-red-50 p-3 rounded-lg mb-4">
              {error}
            </p>
          )}

          <button
            type="button"
            onClick={handleConfirm}
            disabled={submitting}
            className={`w-full bg-blue-900 text-white font-bold py-3 rounded-lg hover:bg-blue-800 transition shadow-lg flex items-center justify-center disabled:opacity-50 disabled:cursor-not-allowed ${BUTTON_FOCUS}`}
          >
            {submitting ? (
              <><Loader2 size={18} className="animate-spin mr-2" aria-hidden="true" /> Confirming...</>
            ) : (
              <><CheckCircle size={18} className="mr-2" aria-hidden="true" /> Confirm Appointment</>
            )}
          </button>
        </section>
      )}
    </main>
  );
};

export default Appointments;
