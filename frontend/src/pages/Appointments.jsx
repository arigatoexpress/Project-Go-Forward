import React, { useState, useEffect } from 'react';
import { Calendar, Clock, User, CheckCircle, ArrowLeft, ArrowRight, MapPin, Phone, Loader2, Download } from 'lucide-react';
import { BUSINESS_NAME, BUSINESS_PHONE, BUSINESS_ADDRESS, BUSINESS_CITY, BUSINESS_STATE } from '../constants';

const BUSINESS_FULL_ADDRESS = `${BUSINESS_ADDRESS}, ${BUSINESS_CITY}, ${BUSINESS_STATE}`;

const DAYS = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'];
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
    <div className="flex items-center justify-center mb-8">
      {steps.map((step, i) => {
        const Icon = step.icon;
        const isActive = currentStep === step.num;
        const isComplete = currentStep > step.num;
        return (
          <React.Fragment key={step.num}>
            <div className="flex flex-col items-center">
              <div className={`w-10 h-10 rounded-full flex items-center justify-center text-sm font-bold transition-colors
                ${isComplete ? 'bg-green-500 text-white' : isActive ? 'bg-blue-600 text-white' : 'bg-gray-200 text-gray-500'}`}>
                {isComplete ? <CheckCircle size={18} /> : <Icon size={18} />}
              </div>
              <span className={`text-xs mt-1 ${isActive ? 'text-blue-600 font-semibold' : 'text-gray-400'}`}>{step.label}</span>
            </div>
            {i < steps.length - 1 && (
              <div className={`w-12 sm:w-20 h-0.5 mx-1 mt-[-12px] ${currentStep > step.num ? 'bg-green-500' : 'bg-gray-200'}`} />
            )}
          </React.Fragment>
        );
      })}
    </div>
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

    cells.push(
      <button
        key={day}
        disabled={disabled}
        onClick={() => onSelect(d.toISOString().slice(0, 10))}
        className={`
          aspect-square rounded-lg text-sm font-medium transition-all
          ${disabled ? 'text-gray-300 cursor-not-allowed' : 'hover:bg-blue-100 cursor-pointer'}
          ${isSelected ? 'bg-blue-600 text-white hover:bg-blue-700' : ''}
          ${isToday && !isSelected ? 'ring-2 ring-blue-400 text-blue-600 font-bold' : ''}
          ${!disabled && !isSelected && !isToday ? 'text-gray-700' : ''}
        `}
      >
        {day}
      </button>
    );
  }

  return (
    <div className="bg-white rounded-xl shadow-md border border-gray-100 p-6">
      <div className="flex items-center justify-between mb-4">
        <button
          onClick={() => {
            if (viewMonth === 0) { setViewMonth(11); setViewYear(viewYear - 1); }
            else setViewMonth(viewMonth - 1);
          }}
          disabled={!canGoPrev}
          className="p-2 rounded-full hover:bg-gray-100 disabled:opacity-30 disabled:cursor-not-allowed transition"
        >
          <ArrowLeft size={20} />
        </button>
        <h3 className="text-lg font-bold text-gray-900">{MONTHS[viewMonth]} {viewYear}</h3>
        <button
          onClick={() => {
            if (viewMonth === 11) { setViewMonth(0); setViewYear(viewYear + 1); }
            else setViewMonth(viewMonth + 1);
          }}
          disabled={!canGoNext}
          className="p-2 rounded-full hover:bg-gray-100 disabled:opacity-30 disabled:cursor-not-allowed transition"
        >
          <ArrowRight size={20} />
        </button>
      </div>
      <div className="grid grid-cols-7 gap-1 mb-2">
        {DAYS.map(d => (
          <div key={d} className="text-center text-xs font-semibold text-gray-400 py-1">{d}</div>
        ))}
      </div>
      <div className="grid grid-cols-7 gap-1">
        {cells}
      </div>
    </div>
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
      <div className="text-center py-12">
        <Loader2 className="h-8 w-8 animate-spin text-blue-600 mx-auto mb-3" />
        <p className="text-gray-500">Loading available times...</p>
      </div>
    );
  }

  if (error) {
    return (
      <div className="text-center py-12">
        <p className="text-red-600 mb-4">{error}</p>
        <button onClick={onBack} className="text-blue-600 hover:underline text-sm">Choose a different date</button>
      </div>
    );
  }

  return (
    <div className="bg-white rounded-xl shadow-md border border-gray-100 p-6">
      <div className="flex items-center justify-between mb-4">
        <button onClick={onBack} className="text-sm text-blue-600 hover:underline flex items-center">
          <ArrowLeft size={14} className="mr-1" /> Change date
        </button>
        <span className="text-sm text-gray-500">{slots.business_hours}</span>
      </div>
      <h3 className="text-lg font-bold text-gray-900 mb-1">{dateLabel}</h3>
      <p className="text-sm text-gray-500 mb-4">Select a time for your visit</p>

      {slots.available_slots.length === 0 ? (
        <div className="text-center py-8">
          <p className="text-gray-500 mb-2">No available times for this date.</p>
          <button onClick={onBack} className="text-blue-600 hover:underline text-sm">Choose a different date</button>
        </div>
      ) : (
        <div className="grid grid-cols-2 sm:grid-cols-3 gap-3">
          {slots.available_slots.map(slot => (
            <button
              key={slot}
              onClick={() => onSelect(slot)}
              className="px-4 py-3 border border-gray-200 rounded-lg text-sm font-medium text-gray-700 hover:border-blue-500 hover:bg-blue-50 hover:text-blue-700 transition"
            >
              <Clock size={14} className="inline mr-2 text-gray-400" />
              {slot}
            </button>
          ))}
        </div>
      )}
    </div>
  );
};

// Contact info form
const ContactForm = ({ formData, onChange, onSubmit, onBack, submitting }) => {
  return (
    <div className="bg-white rounded-xl shadow-md border border-gray-100 p-6">
      <button onClick={onBack} className="text-sm text-blue-600 hover:underline flex items-center mb-4">
        <ArrowLeft size={14} className="mr-1" /> Change time
      </button>
      <h3 className="text-lg font-bold text-gray-900 mb-4">Your Information</h3>
      <form onSubmit={onSubmit} className="space-y-4">
        <div>
          <label className="block text-sm font-medium text-gray-700 mb-1">Full Name *</label>
          <input
            type="text"
            required
            value={formData.name}
            onChange={(e) => onChange({ ...formData, name: e.target.value })}
            className="w-full px-4 py-2.5 border border-gray-300 rounded-lg focus:ring-2 focus:ring-blue-500 outline-none"
            placeholder="John Doe"
          />
        </div>
        <div>
          <label className="block text-sm font-medium text-gray-700 mb-1">Phone Number *</label>
          <input
            type="tel"
            required
            value={formData.phone}
            onChange={(e) => onChange({ ...formData, phone: e.target.value })}
            className="w-full px-4 py-2.5 border border-gray-300 rounded-lg focus:ring-2 focus:ring-blue-500 outline-none"
            placeholder="(281) 000-0000"
          />
        </div>
        <div>
          <label className="block text-sm font-medium text-gray-700 mb-1">Email (optional)</label>
          <input
            type="email"
            value={formData.email}
            onChange={(e) => onChange({ ...formData, email: e.target.value })}
            className="w-full px-4 py-2.5 border border-gray-300 rounded-lg focus:ring-2 focus:ring-blue-500 outline-none"
            placeholder="john@example.com"
          />
        </div>
        <div>
          <label className="block text-sm font-medium text-gray-700 mb-1">What homes are you interested in?</label>
          <textarea
            rows="3"
            value={formData.notes}
            onChange={(e) => onChange({ ...formData, notes: e.target.value })}
            className="w-full px-4 py-2.5 border border-gray-300 rounded-lg focus:ring-2 focus:ring-blue-500 outline-none"
            placeholder="e.g., 3 bedroom double wide, budget under $100k..."
          />
        </div>
        <button
          type="submit"
          disabled={submitting || !formData.name.trim() || !formData.phone.trim()}
          className="w-full bg-blue-600 text-white font-bold py-3 rounded-lg hover:bg-blue-700 transition shadow-md disabled:opacity-50 disabled:cursor-not-allowed flex items-center justify-center"
        >
          {submitting ? (
            <><Loader2 size={18} className="animate-spin mr-2" /> Booking...</>
          ) : (
            'Review & Confirm'
          )}
        </button>
      </form>
    </div>
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
      <div className="max-w-lg mx-auto px-4 py-12 text-center">
        <div className="bg-white p-8 rounded-2xl shadow-xl border border-gray-100">
          <div className="bg-green-100 p-4 rounded-full inline-block mb-4">
            <CheckCircle className="h-12 w-12 text-green-600" />
          </div>
          <h2 className="text-2xl font-bold text-gray-900 mb-2">Appointment Confirmed!</h2>
          <p className="text-gray-600 mb-6">We look forward to seeing you.</p>

          <div className="bg-gray-50 rounded-xl p-4 text-left space-y-2 mb-6">
            <div className="flex items-center text-sm">
              <Calendar size={16} className="text-blue-600 mr-3 flex-shrink-0" />
              <span className="font-medium">{dateLabel}</span>
            </div>
            <div className="flex items-center text-sm">
              <Clock size={16} className="text-blue-600 mr-3 flex-shrink-0" />
              <span className="font-medium">{selectedTime}</span>
            </div>
            <div className="flex items-center text-sm">
              <MapPin size={16} className="text-blue-600 mr-3 flex-shrink-0" />
              <span>{BUSINESS_FULL_ADDRESS}</span>
            </div>
            <div className="flex items-center text-sm">
              <Phone size={16} className="text-blue-600 mr-3 flex-shrink-0" />
              <span>{BUSINESS_PHONE}</span>
            </div>
          </div>

          <div className="flex flex-col sm:flex-row gap-3">
            <button
              onClick={handleDownloadICS}
              className="flex-1 flex items-center justify-center px-4 py-2.5 border border-blue-600 text-blue-600 rounded-lg hover:bg-blue-50 transition font-medium text-sm"
            >
              <Download size={16} className="mr-2" /> Add to Calendar
            </button>
            <button
              onClick={onBack}
              className="flex-1 bg-blue-600 text-white px-4 py-2.5 rounded-lg hover:bg-blue-700 transition font-medium text-sm"
            >
              Return Home
            </button>
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className="max-w-2xl mx-auto px-4 sm:px-6 py-8">
      <div className="mb-6 flex items-center justify-between">
        <div>
          <h2 className="text-2xl sm:text-3xl font-bold text-blue-900">Book an Appointment</h2>
          <p className="text-gray-500 text-sm mt-1">Schedule a showroom visit at {BUSINESS_NAME}</p>
        </div>
        <button onClick={onBack} className="text-sm font-medium text-blue-600 hover:text-blue-800">
          &larr; Back
        </button>
      </div>

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
        <div className="bg-white rounded-xl shadow-md border border-gray-100 p-6">
          <button onClick={() => setStep(3)} className="text-sm text-blue-600 hover:underline flex items-center mb-4">
            <ArrowLeft size={14} className="mr-1" /> Edit info
          </button>
          <h3 className="text-lg font-bold text-gray-900 mb-4">Review Your Appointment</h3>

          <div className="space-y-3 mb-6">
            <div className="flex justify-between py-2 border-b border-gray-100">
              <span className="text-gray-500 text-sm">Date</span>
              <span className="font-medium text-sm">{dateLabel}</span>
            </div>
            <div className="flex justify-between py-2 border-b border-gray-100">
              <span className="text-gray-500 text-sm">Time</span>
              <span className="font-medium text-sm">{selectedTime}</span>
            </div>
            <div className="flex justify-between py-2 border-b border-gray-100">
              <span className="text-gray-500 text-sm">Name</span>
              <span className="font-medium text-sm">{formData.name}</span>
            </div>
            <div className="flex justify-between py-2 border-b border-gray-100">
              <span className="text-gray-500 text-sm">Phone</span>
              <span className="font-medium text-sm">{formData.phone}</span>
            </div>
            {formData.email && (
              <div className="flex justify-between py-2 border-b border-gray-100">
                <span className="text-gray-500 text-sm">Email</span>
                <span className="font-medium text-sm">{formData.email}</span>
              </div>
            )}
            {formData.notes && (
              <div className="flex justify-between py-2 border-b border-gray-100">
                <span className="text-gray-500 text-sm">Notes</span>
                <span className="font-medium text-sm text-right max-w-[60%]">{formData.notes}</span>
              </div>
            )}
            <div className="flex justify-between py-2">
              <span className="text-gray-500 text-sm">Location</span>
              <span className="font-medium text-sm">{BUSINESS_FULL_ADDRESS}</span>
            </div>
          </div>

          {error && (
            <p className="text-sm text-red-600 bg-red-50 p-3 rounded-lg mb-4">{error}</p>
          )}

          <button
            onClick={handleConfirm}
            disabled={submitting}
            className="w-full bg-blue-900 text-white font-bold py-3 rounded-lg hover:bg-blue-800 transition shadow-lg flex items-center justify-center disabled:opacity-50 disabled:cursor-not-allowed"
          >
            {submitting ? (
              <><Loader2 size={18} className="animate-spin mr-2" /> Confirming...</>
            ) : (
              <><CheckCircle size={18} className="mr-2" /> Confirm Appointment</>
            )}
          </button>
        </div>
      )}
    </div>
  );
};

export default Appointments;
