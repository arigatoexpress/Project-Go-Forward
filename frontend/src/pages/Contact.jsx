import React, { useState } from 'react';
import { Mail, Phone, MapPin, Send, Loader2 } from 'lucide-react';
import { BUSINESS_NAME, BUSINESS_URL, BUSINESS_PHONE, BUSINESS_PHONE_RAW, BUSINESS_FULL_ADDRESS, BUSINESS_HOURS } from '../constants';
import { getUtmParams } from '../utils/utm';
import { trackEvent } from '../utils/analytics';
import AppointmentHandoffCard from '../components/AppointmentHandoffCard';

const Contact = ({ onBack, onBookAppointment }) => {
    const [submitted, setSubmitted] = useState(false);
    const [submitting, setSubmitting] = useState(false);
    const [formData, setFormData] = useState({ name: '', phone: '', email: '', message: '' });
    const [submitError, setSubmitError] = useState('');
    const [persistedLeadId, setPersistedLeadId] = useState('');

    const phoneDigits = formData.phone.replace(/\D/g, '');
    const isPhoneValid = phoneDigits.length >= 10;
    // Soft hint only — email is optional and NEVER gates submission.
    const emailLooksValid = /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(formData.email.trim());
    const showEmailHint = formData.email.trim() !== '' && !emailLooksValid;

    const handleSubmit = async (e) => {
        e.preventDefault();
        if (!isPhoneValid) {
            setSubmitError('Please enter a valid 10-digit phone number.');
            return;
        }
        setSubmitting(true);
        setSubmitError('');
        try {
            const resp = await fetch('/api/contact', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ ...formData, ...getUtmParams() }),
            });
            const data = await resp.json();
            if (data.success) {
                setPersistedLeadId(data.lead_id || '');
                setSubmitted(true);
                trackEvent('lead_captured', { source: 'contact', type: 'contact' });
            } else {
                setSubmitError(data.error || 'Something went wrong. Please try again.');
            }
        } catch {
            setSubmitError('Unable to send your message. Please call us instead.');
        } finally {
            setSubmitting(false);
        }
    };

    const handleBookAppointment = () => {
        trackEvent('appointment_handoff_started', { source: 'contact_handoff', intent: 'contact' });
        onBookAppointment?.({
            name: formData.name,
            phone: formData.phone,
            email: formData.email,
            notes: formData.message,
            leadId: persistedLeadId,
            source: 'contact_handoff',
            intent: 'contact',
        });
    };

    if (submitted) {
        return (
            <div className="mx-auto max-w-4xl p-6 py-14 sm:py-20">
                <div className="flex justify-center rounded-2xl border border-[var(--cp-border)] bg-[var(--cp-panel)] p-6 shadow-xl sm:p-8">
                    <AppointmentHandoffCard
                        title="Message received"
                        description={`A member of the ${BUSINESS_NAME} family will contact you shortly. You can also reserve a showroom time now while your plans are fresh.`}
                        onStart={handleBookAppointment}
                        onContinue={onBack}
                        continueLabel="Return home"
                    />
                </div>
            </div>
        );
    }

    return (
        <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-12">
            <div className="mb-8 flex items-center justify-between">
                <div>
                    <h2 className="text-3xl font-bold text-blue-900">Contact Us</h2>
                    <p className="text-gray-600">We're here to help you find your dream home.</p>
                </div>
                <button
                    onClick={onBack}
                    className="text-sm font-medium text-blue-600 hover:text-blue-800"
                >
                    &larr; Back to Chat
                </button>
            </div>

            <div className="grid grid-cols-1 md:grid-cols-2 gap-12">
                {/* Contact Info */}
                <div className="space-y-8">
                    <div className="bg-white p-6 rounded-xl shadow-md border border-gray-100">
                        <div className="flex items-start space-x-4">
                            <div className="bg-blue-100 p-3 rounded-full">
                                <MapPin className="h-6 w-6 text-blue-600" />
                            </div>
                            <div>
                                <h3 className="text-lg font-bold text-gray-900">Visit Our Showroom</h3>
                                <p className="text-gray-700 mt-1 font-medium">{BUSINESS_FULL_ADDRESS}</p>
                                <a
                                    href={`https://www.google.com/maps/search/?api=1&query=${encodeURIComponent(BUSINESS_FULL_ADDRESS)}`}
                                    target="_blank"
                                    rel="noreferrer"
                                    className="text-sm text-blue-700 hover:text-blue-900 hover:underline mt-2 inline-block"
                                >
                                    Get directions
                                </a>
                            </div>
                        </div>
                    </div>

                    <div className="bg-white p-6 rounded-xl shadow-md border border-gray-100">
                        <div className="flex items-start space-x-4">
                            <div className="bg-red-100 p-3 rounded-full">
                                <Phone className="h-6 w-6 text-red-600" />
                            </div>
                            <div>
                                <h3 className="text-lg font-bold text-gray-900">Call Us Directly</h3>
                                <a href={`tel:${BUSINESS_PHONE_RAW}`} className="text-gray-600 mt-1 block hover:text-blue-600 transition-colors">{BUSINESS_PHONE}</a>
                                <p className="text-xs text-gray-500 mt-1">{BUSINESS_HOURS}</p>
                            </div>
                        </div>
                    </div>

                    <div className="bg-white p-6 rounded-xl shadow-md border border-gray-100">
                        <div className="flex items-start space-x-4">
                            <div className="bg-green-100 p-3 rounded-full">
                                <Mail className="h-6 w-6 text-green-600" />
                            </div>
                            <div>
                                <h3 className="text-lg font-bold text-gray-900">Email Our Team</h3>
                                <p className="text-gray-600 mt-1">sales@{BUSINESS_URL}</p>
                            </div>
                        </div>
                    </div>
                </div>

                {/* Contact Form */}
                <div className="bg-white p-8 rounded-2xl shadow-xl border border-gray-100">
                    <h3 className="text-xl font-bold text-gray-900 mb-6">Send a Message</h3>
                    <fieldset disabled={submitting}>
                    <form onSubmit={handleSubmit} className="space-y-4">
                        <div>
                            <label htmlFor="contact-name" className="block text-sm font-medium text-gray-700 mb-1">Your Name</label>
                            <input
                                id="contact-name"
                                type="text"
                                autoComplete="name"
                                required
                                value={formData.name}
                                onChange={(e) => setFormData(prev => ({ ...prev, name: e.target.value }))}
                                className="w-full px-4 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-blue-500 outline-none"
                                placeholder="John Doe"
                            />
                        </div>
                        <div>
                            <label htmlFor="contact-phone" className="block text-sm font-medium text-gray-700 mb-1">Phone Number</label>
                            <input
                                id="contact-phone"
                                type="tel"
                                autoComplete="tel"
                                required
                                value={formData.phone}
                                onChange={(e) => {
                                    setFormData(prev => ({ ...prev, phone: e.target.value }));
                                    if (submitError) setSubmitError('');
                                }}
                                className={`w-full px-4 py-2 border rounded-lg focus:ring-2 focus:ring-blue-500 outline-none ${formData.phone && !isPhoneValid ? 'border-red-300' : 'border-gray-300'}`}
                                placeholder="(281) 000-0000"
                                aria-describedby={formData.phone && !isPhoneValid ? 'contact-phone-error' : undefined}
                            />
                            {formData.phone && !isPhoneValid && (
                                <p id="contact-phone-error" className="text-xs text-red-700 mt-1">Enter a valid 10-digit phone number</p>
                            )}
                        </div>
                        <div>
                            <label htmlFor="contact-email" className="block text-sm font-medium text-gray-700 mb-1">Email (optional)</label>
                            <input
                                id="contact-email"
                                type="email"
                                autoComplete="email"
                                value={formData.email}
                                onChange={(e) => setFormData(prev => ({ ...prev, email: e.target.value }))}
                                className={`w-full px-4 py-2 border rounded-lg focus:ring-2 focus:ring-blue-500 outline-none ${showEmailHint ? 'border-amber-300' : 'border-gray-300'}`}
                                placeholder="john@example.com"
                                aria-describedby={showEmailHint ? 'contact-email-hint' : undefined}
                            />
                            {showEmailHint && (
                                <p id="contact-email-hint" className="text-xs text-amber-700 mt-1">This email looks incomplete. You can still send without it.</p>
                            )}
                        </div>
                        <div>
                            <label htmlFor="contact-message" className="block text-sm font-medium text-gray-700 mb-1">What can we help with?</label>
                            <textarea
                                id="contact-message"
                                rows="4"
                                required
                                value={formData.message}
                                onChange={(e) => setFormData(prev => ({ ...prev, message: e.target.value }))}
                                className="w-full px-4 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-blue-500 outline-none"
                                placeholder="Tell us about the home you are looking for..."
                                maxLength={2000}
                            ></textarea>
                            <p className="text-xs text-gray-500 text-right mt-1">{formData.message.length}/2000</p>
                        </div>
                        {submitError && (
                            <p role="alert" className="text-sm text-red-600 bg-red-50 p-3 rounded-lg">{submitError}</p>
                        )}
                        <button
                            type="submit"
                            disabled={submitting}
                            className="w-full bg-blue-900 text-white font-bold py-3 rounded-lg hover:bg-blue-800 active:scale-[0.98] transition shadow-lg flex items-center justify-center space-x-2 disabled:opacity-40 disabled:cursor-not-allowed"
                        >
                            {submitting ? <Loader2 size={18} className="animate-spin" /> : <Send size={18} />}
                            <span>{submitting ? 'Sending...' : 'Send Message'}</span>
                        </button>
                    </form>
                    </fieldset>
                </div>
            </div>
        </div>
    );
};

export default Contact;
