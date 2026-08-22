import React from 'react';
import { View, Text, StyleSheet } from 'react-native';
import { Card } from '../../components/ui';
import { colors, type } from '../../theme';

const POINTS = [
  ['⏱', 'Takes about 5 minutes', 'A few questions about a normal day — sleep, routines, who to call.'],
  ['🧭', 'This builds your personal baseline', 'The wearable compares against it, so a Sunday sleep-in is not treated like an emergency.'],
  ['🔒', 'You stay in control', 'Everything can be changed later, and nothing is shared without your consent.'],
];

export default function WelcomeStep() {
  return (
    <View>
      <Text style={[type.body, styles.lead]}>
        This short setup builds a picture of what a normal day looks like for you. It is
        what lets the wearable tell the difference between an ordinary quiet morning and
        something being wrong — and know exactly who to call if it is.
      </Text>
      {POINTS.map(([icon, title, body]) => (
        <Card key={title} style={styles.pointCard}>
          <Text style={styles.pointIcon}>{icon}</Text>
          <View style={{ flex: 1 }}>
            <Text style={type.label}>{title}</Text>
            <Text style={[type.small, { marginTop: 4 }]}>{body}</Text>
          </View>
        </Card>
      ))}
    </View>
  );
}

const styles = StyleSheet.create({
  lead: { marginBottom: 20, color: colors.text },
  pointCard: { flexDirection: 'row', alignItems: 'center', gap: 14 },
  pointIcon: { fontSize: 26 },
});
