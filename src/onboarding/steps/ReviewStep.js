import React from 'react';
import { View, Text, StyleSheet } from 'react-native';
import { Card, InfoBanner } from '../../components/ui';
import {
  DAYS, DAY_LABELS, SEX_OPTIONS, LIVING_SITUATION_OPTIONS, MOBILITY_OPTIONS,
  FREQUENCY_OPTIONS, SHARE_OPTIONS, DIET_OPTIONS, labelFor,
} from '../../model/profile';
import { parseTime, parseDob, formatTime, ageFromDob } from '../../utils/datetime';
import { colors, type } from '../../theme';

function Section({ title, stepKey, jumpTo, children }) {
  return (
    <Card>
      <View style={styles.sectionHeader}>
        <Text style={type.label}>{title}</Text>
        <Text style={styles.editLink} onPress={() => jumpTo(stepKey)}>
          Edit
        </Text>
      </View>
      {children}
    </Card>
  );
}

function Row({ label, value }) {
  return (
    <View style={styles.row}>
      <Text style={styles.rowLabel}>{label}</Text>
      <Text style={styles.rowValue}>{value || '—'}</Text>
    </View>
  );
}

const displayTime = (raw) => {
  const parsed = parseTime(raw);
  return parsed ? formatTime(parsed) : raw || '—';
};

export default function ReviewStep({ draft, jumpTo, finishProblems }) {
  const d = draft.demographics;
  const dobIso = parseDob(d.dobText);
  const contacts = [
    { ...draft.contacts.nextOfKin, tag: 'Primary' },
    ...(draft.contacts.secondary.enabled ? [{ ...draft.contacts.secondary, tag: 'Secondary' }] : []),
    ...(draft.contacts.gp.enabled ? [{ ...draft.contacts.gp, tag: 'GP' }] : []),
  ];
  const routineDays = DAYS.filter((day) => draft.weeklyRoutine[day].length > 0);
  const { smoking, alcohol } = draft.lifestyle;

  return (
    <View>
      <Text style={[type.subtitle, { marginBottom: 16 }]}>
        Check everything looks right. Tap Edit to change a section — you can also update
        it any time after setup.
      </Text>

      {finishProblems.length > 0 && (
        <InfoBanner tone="danger">{finishProblems.join('\n')}</InfoBanner>
      )}

      <Section title="About you" stepKey="demographics" jumpTo={jumpTo}>
        <Row label="Name" value={d.name} />
        <Row label="Sex" value={labelFor(SEX_OPTIONS, d.sex)} />
        <Row label="Date of birth" value={dobIso ? `${d.dobText} (age ${ageFromDob(dobIso)})` : d.dobText} />
        <Row label="Living situation" value={labelFor(LIVING_SITUATION_OPTIONS, d.livingSituation)} />
      </Section>

      <Section title="Emergency contacts" stepKey="contacts" jumpTo={jumpTo}>
        {contacts.map((c) => (
          <Row key={c.tag} label={c.tag} value={`${c.name} · ${c.relationship} · ${c.phone}`} />
        ))}
      </Section>

      <Section title="Sleep" stepKey="sleep" jumpTo={jumpTo}>
        <Row label="Wakes" value={displayTime(draft.sleep.typicalWake)} />
        <Row label="Sleeps" value={displayTime(draft.sleep.typicalSleep)} />
        <Row label="Naps" value={draft.sleep.napPattern.trim()} />
      </Section>

      <Section title="Weekly routine" stepKey="routine" jumpTo={jumpTo}>
        {routineDays.length === 0 ? (
          <Text style={type.small}>No scheduled activities added.</Text>
        ) : (
          routineDays.map((day) => (
            <Row
              key={day}
              label={DAY_LABELS[day]}
              value={draft.weeklyRoutine[day]
                .map((e) => `${e.activity} (${formatTime(e.expectedTime)}, ~${e.expectedDuration}m)`)
                .join(', ')}
            />
          ))
        )}
      </Section>

      <Section title="Hobbies & mobility" stepKey="hobbiesMobility" jumpTo={jumpTo}>
        <Row label="Hobbies" value={draft.hobbies.join(', ')} />
        <Row label="Mobility" value={labelFor(MOBILITY_OPTIONS, draft.mobilityLevel)} />
      </Section>

      <Section title="Lifestyle" stepKey="lifestyle" jumpTo={jumpTo}>
        <Row
          label="Diet"
          value={[draft.lifestyle.diet ? labelFor(DIET_OPTIONS, draft.lifestyle.diet) : null, draft.lifestyle.dietNote.trim() || null]
            .filter(Boolean)
            .join(' — ')}
        />
        <Row label="Smokes" value={smoking.status ? labelFor(FREQUENCY_OPTIONS, smoking.frequency) : 'No'} />
        <Row label="Alcohol" value={alcohol.status ? labelFor(FREQUENCY_OPTIONS, alcohol.frequency) : 'No'} />
      </Section>

      <Section title="Health context" stepKey="health" jumpTo={jumpTo}>
        <Row label="Shared conditions" value={draft.healthContext.join(', ')} />
        <Row
          label="Medications"
          value={draft.medicationCount === null ? 'Not set' : String(draft.medicationCount)}
        />
      </Section>

      <Section title="Consent & privacy" stepKey="consent" jumpTo={jumpTo}>
        <Row label="Monitoring" value={draft.consent.monitoringConsent ? 'Consented' : 'Not consented'} />
        <Row
          label="Visible to"
          value={draft.consent.sharedWith.map((v) => labelFor(SHARE_OPTIONS, v)).join(', ')}
        />
      </Section>
    </View>
  );
}

const styles = StyleSheet.create({
  sectionHeader: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'center',
    marginBottom: 10,
  },
  editLink: { color: colors.primary, fontSize: 15, fontWeight: '700', padding: 4 },
  row: { flexDirection: 'row', marginBottom: 6 },
  rowLabel: { ...type.small, fontSize: 15, width: 110 },
  rowValue: { ...type.body, fontSize: 15, flex: 1 },
});
