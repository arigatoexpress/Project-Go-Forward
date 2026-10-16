import React from 'react';
import { Camera, ClipboardList, FileText, Users } from 'lucide-react';

const TASKS = [
  { key: 'crm', label: 'Answer a lead', hint: 'Call or write someone who asked about a home', icon: Users },
  { key: 'manage-inventory', label: 'Add or change a home', hint: 'Set the price and add photos of the real home', icon: ClipboardList },
  { key: 'photos', label: 'Add Photos', hint: 'Put lot photos on several homes', icon: Camera },
  { key: 'documents', label: 'Documents', hint: 'Fill and share paperwork', icon: FileText },
];

export default function StaffHome({ onNavigate }) {
  return (
    <div className="max-w-3xl mx-auto px-4 py-8 text-[var(--cp-text)]">
      <h1 className="text-3xl font-bold mb-2">Staff Home</h1>
      <p className="text-lg text-[var(--cp-muted)] mb-8 leading-relaxed">
        Pick one job. The two at the top are the ones you will use most: answer a person, or add a home with real photos.
      </p>
      <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
        {TASKS.map((task) => {
          const Icon = task.icon;
          return (
            <button
              key={task.key}
              type="button"
              onClick={() => onNavigate(task.key)}
              className="flex items-start gap-4 rounded-2xl border border-[var(--cp-border)] bg-[var(--cp-panel)] p-6 text-left shadow-sm hover:border-[var(--cp-accent)] hover:bg-[var(--cp-surface)] min-h-32"
            >
              <span className="rounded-xl bg-[var(--cp-accent-dim)] p-3 text-[var(--cp-accent)]">
                <Icon size={28} aria-hidden="true" />
              </span>
              <span>
                <span className="block text-xl font-semibold">{task.label}</span>
                <span className="block mt-1 text-base text-[var(--cp-muted)] leading-relaxed">{task.hint}</span>
              </span>
            </button>
          );
        })}
      </div>
    </div>
  );
}
