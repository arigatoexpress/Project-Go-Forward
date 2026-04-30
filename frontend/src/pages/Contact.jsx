import React, { useState } from 'react';
import { Mail, Phone, MapPin, Send, CheckCircle, Loader2 } from 'lucide-react';
import { BUSINESS_NAME, BUSINESS_URL, BUSINESS_PHONE, BUSINESS_PHONE_RAW, BUSINESS_ADDRESS, BUSINESS_CITY, BUSINESS_HOURS } from '../constants';

// Shared focus styles applied to every input/textarea so keyboard users get a
// clear visual focus indicator. Tailwind's `focus-visible:` keeps mouse clicks
// quiet while still meeting WCAG 2.4.7.
const FIELD_BASE = 'w-full px-4 py-2 border rounded-lg outline-none transition focus-visible:ring-2 focus-visible:ring-blue-500 focus-visible:border-blue-500';
const BUTTON_FOCUS = 'focus:outline-none focus-visible:ring-2 focus-visible:ring-offset-2 focus-visible:ring-blue-500';

const Contact = ({ onBack }) => {
    const [submitted, setSubmitted] = useState(false);
    const [submitting, setSubmitting] = useState(false);
    const [formData, setFormData] = useState({ name: '', phone: '', email: '', message: '' });
    const [submitError, setSubmitError] = useState('');

    const phoneDigits = formData.phone.replace(/\D/g, '');
    const isPhoneValid = phoneDigits.length >= 10;
    const showPhoneError = formData.phone && !isPhoneValid;

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
                body: JSON.stringify(formData),
            });
            const data = await resp.json();
            if (data.success) {
                setSubmitted(true);
            } else {
                setSubmitError(data.error || 'Something went wrong. Please try again.');
            }
        } catch {
            setSubmitError('Unable to send your message. Please call us instead.');
        } finally {
            setSubmitting(false);
        }
    };

    if (submitted) {
        return (
            <main className="max-w-4xl mx-auto p-6 text-center py-20" aria-labelledby="contact-confirm-heading">
                <div className="bg-white p-8 rounded-2xl shadow-xl inline-block">
                    <CheckCircle className="h-16 w-16 text-green-500 mx-auto mb-4" aria-hidden="true" />
                    <h1 id="contact-confirm-heading" className="text-2xl font-bold text-gray-900 mb-2">Message Received!</h1>
                    <p className="text-gray-600 mb-6">Thank you for reaching out. A member of the {BUSINESS_NAME} family will contact you shortly.</p>
                    <button
                        type="button"
                        onClick={onBack}
                        className={`bg-blue-600 text-white px-6 py-2 rounded-full hover:bg-blue-700 transition ${BUTTON_FOCUS}`}
                    >
                        Return Home
                    </button>
                </div>
            </main>
        );
    }

    return (
        <main className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-12" aria-labelledby="contact-heading">
            <header className="mb-8 flex items-center justify-between">
                <div>
                    <h1 id="contact-heading" className="text-3xl font-bold text-blue-900">Contact Us</h1>
                    <p className="text-gray-600">We're here to help you find your dream home.</p>
                </div>
                <button
                    type="button"
                    onClick={onBack}
                    className={`text-sm font-medium text-blue-600 hover:text-blue-800 rounded ${BUTTON_FOCUS}`}
                >
                    &larr; Back to Chat
                </button>
            </header>

            <div className="grid grid-cols-1 md:grid-cols-2 gap-12">
                {/* Contact Info */}
                <section aria-labelledby="contact-info-heading" className="space-y-8">
                    <h2 id="contact-info-heading" className="sr-only">Contact information</h2>
                    <div className="bg-white p-6 rounded-xl shadow-md border border-gray-100">
                        <div className="flex items-start space-x-4">
                            <div className="bg-blue-100 p-3 rounded-full" aria-hidden="true">
                                <MapPin className="h-6 w-6 text-blue-600" />
                            </div>
                            <div>
                                <h3 className="text-lg font-bold text-gray-900">Visit Our Showroom</h3>
                                <p className="text-gray-600 mt-1">{BUSINESS_ADDRESS}<br />{BUSINESS_CITY}</p>
                            </div>
                        </div>
                    </div>

                    <div className="bg-white p-6 rounded-xl shadow-md border border-gray-100">
                        <div className="flex items-start space-x-4">
                            <div className="bg-red-100 p-3 rounded-full" aria-hidden="true">
                                <Phone className="h-6 w-6 text-red-600" />
                            </div>
                            <div>
                                <h3 className="text-lg font-bold text-gray-900">Call Us Directly</h3>
                                <a
                                    href={`tel:${BUSINESS_PHONE_RAW}`}
                                    className={`text-gray-700 mt-1 block hover:text-blue-700 transition-colors rounded ${BUTTON_FOCUS}`}
                                >
                                    {BUSINESS_PHONE}
                                </a>
                                <p className="text-xs text-gray-500 mt-1">{BUSINESS_HOURS}</p>
                            </div>
                        </div>
                    </div>

                    <div className="bg-white p-6 rounded-xl shadow-md border border-gray-100">
                        <div className="flex items-start space-x-4">
                            <div className="bg-green-100 p-3 rounded-full" aria-hidden="true">
                                <Mail className="h-6 w-6 text-green-600" />
                            </div>
                            <div>
                                <h3 className="text-lg font-bold text-gray-900">Email Our Team</h3>
                                <p className="text-gray-700 mt-1">sales@{BUSINESS_URL}</p>
                            </div>
                        </div>
                    </div>
                </section>

                {/* Contact Form */}
                <section aria-labelledby="contact-form-heading" className="bg-white p-8 rounded-2xl shadow-xl border border-gray-100">
                    <h2 id="contact-form-heading" className="text-xl font-bold text-gray-900 mb-6">Send a Message</h2>
                    <form onSubmit={handleSubmit} className="space-y-4" noValidate>
                        <fieldset disabled={submitting} className="space-y-4 border-0 p-0 m-0">
                            <legend className="sr-only">Contact form fields</legend>
                            <div>
                                <label htmlFor="contact-name" className="block text-sm font-medium text-gray-700 mb-1">
                                    Your Name <span className="text-red-600" aria-hidden="true">*</span>
                                </label>
                                <input
                                    id="contact-name"
                                    name="name"
                                    type="text"
                                    required
                                    aria-required="true"
                                    autoComplete="name"
                                    value={formData.name}
                                    onChange={(e) => setFormData(prev => ({ ...prev, name: e.target.value }))}
                                    className={`${FIELD_BASE} border-gray-300`}
                                    placeholder="John Doe"
                                />
                            </div>
                            <div>
                                <label htmlFor="contact-phone" className="block text-sm font-medium text-gray-700 mb-1">
                                    Phone Number <span className="text-red-600" aria-hidden="true">*</span>
                                </label>
                                <input
                                    id="contact-phone"
                                    name="phone"
                                    type="tel"
                                    required
                                    aria-required="true"
                                    autoComplete="tel"
                                    aria-invalid={showPhoneError ? 'true' : 'false'}
                                    aria-describedby={showPhoneError ? 'contact-phone-error' : undefined}
                                    value={formData.phone}
                                    onChange={(e) => {
                                        setFormData(prev => ({ ...prev, phone: e.target.value }));
                                        if (submitError) setSubmitError('');
                                    }}
                                    className={`${FIELD_BASE} ${showPhoneError ? 'border-red-400' : 'border-gray-300'}`}
                                    placeholder="(281) 000-0000"
                                />
                                {showPhoneError && (
                                    <p id="contact-phone-error" className="text-xs text-red-600 mt-1">
                                        Enter a valid 10-digit phone number
                                    </p>
                                )}
                            </div>
                            <div>
                                <label htmlFor="contact-email" className="block text-sm font-medium text-gray-700 mb-1">
                                    Email <span className="text-gray-500 font-normal">(optional)</span>
                                </label>
                                <input
                                    id="contact-email"
                                    name="email"
                                    type="email"
                                    autoComplete="email"
                                    value={formData.email}
                                    onChange={(e) => setFormData(prev => ({ ...prev, email: e.target.value }))}
                                    className={`${FIELD_BASE} border-gray-300`}
                                    placeholder="john@example.com"
                                />
                            </div>
                            <div>
                                <label htmlFor="contact-message" className="block text-sm font-medium text-gray-700 mb-1">
                                    What can we help with? <span className="text-red-600" aria-hidden="true">*</span>
                                </label>
                                <textarea
                                    id="contact-message"
                                    name="message"
                                    rows="4"
                                    required
                                    aria-required="true"
                                    aria-describedby="contact-message-counter"
                                    value={formData.message}
                                    onChange={(e) => setFormData(prev => ({ ...prev, message: e.target.value }))}
                                    className={`${FIELD_BASE} border-gray-300`}
                                    placeholder="Tell us about the home you are looking for..."
                                    maxLength={2000}
                                />
                                <p
                                    id="contact-message-counter"
                                    className="text-xs text-gray-500 text-right mt-1"
                                    aria-live="polite"
                                >
                                    {formData.message.length}/2000 characters
                                </p>
                            </div>
                            {submitError && (
                                <p
                                    role="alert"
                                    className="text-sm text-red-700 bg-red-50 p-3 rounded-lg"
                                >
                                    {submitError}
                                </p>
                            )}
                            <button
                                type="submit"
                                disabled={submitting}
                                className={`w-full bg-blue-900 text-white font-bold py-3 rounded-lg hover:bg-blue-800 active:scale-[0.98] transition shadow-lg flex items-center justify-center space-x-2 disabled:opacity-40 disabled:cursor-not-allowed ${BUTTON_FOCUS}`}
                            >
                                {submitting
                                    ? <Loader2 size={18} className="animate-spin" aria-hidden="true" />
                                    : <Send size={18} aria-hidden="true" />}
                                <span>{submitting ? 'Sending...' : 'Send Message'}</span>
                            </button>
                        </fieldset>
                    </form>
                </section>
            </div>
        </main>
    );
};

export default Contact;
