#!/usr/bin/env python3
"""
Qardio APK Analysis Utility

This script helps analyze a Qardio APK file to extract Bluetooth-related information
and protocol details.
"""

import os
import sys
import re
import json
import zipfile
import argparse
from pathlib import Path
from typing import Dict, List, Set

class QardioAPKAnalyzer:
    """Analyzes Qardio APK files for Bluetooth protocol information."""
    
    def __init__(self, apk_path: str):
        self.apk_path = Path(apk_path)
        self.extracted_dir = self.apk_path.with_suffix('')
        
        # Common Bluetooth-related keywords and patterns
        self.bluetooth_keywords = [
            'bluetooth', 'ble', 'gatt', 'characteristic', 'service',
            'uuid', 'notification', 'indication', 'read_gatt', 'write_gatt',
            'blood_pressure', 'bp_', 'qardio', 'arm2', 'measurement'
        ]
        
        # Standard Bluetooth UUIDs to look for
        self.standard_uuids = {
            "00001810-0000-1000-8000-00805F9B34FB": "Blood Pressure Service",
            "00002A35-0000-1000-8000-00805F9B34FB": "Blood Pressure Measurement",
            "00002A49-0000-1000-8000-00805F9B34FB": "Blood Pressure Feature", 
            "00002A36-0000-1000-8000-00805F9B34FB": "Intermediate Cuff Pressure",
            "0000180A-0000-1000-8000-00805F9B34FB": "Device Information Service",
            "00002A29-0000-1000-8000-00805F9B34FB": "Manufacturer Name String",
            "00002A24-0000-1000-8000-00805F9B34FB": "Model Number String",
            "00002A25-0000-1000-8000-00805F9B34FB": "Serial Number String",
            "00002A26-0000-1000-8000-00805F9B34FB": "Firmware Revision String",
            "0000180F-0000-1000-8000-00805F9B34FB": "Battery Service",
            "00002A19-0000-1000-8000-00805F9B34FB": "Battery Level"
        }
        
        self.findings = {
            'uuids_found': {},
            'bluetooth_classes': [],
            'interesting_methods': [],
            'constants': {},
            'potential_protocols': []
        }
    
    def extract_apk(self) -> bool:
        """Extract the APK file for analysis."""
        try:
            with zipfile.ZipFile(self.apk_path, 'r') as zip_ref:
                zip_ref.extractall(self.extracted_dir)
            print(f"✅ APK extracted to: {self.extracted_dir}")
            return True
        except Exception as e:
            print(f"❌ Failed to extract APK: {e}")
            return False
    
    def find_java_files(self) -> List[Path]:
        """Find all .java files in the extracted directory."""
        java_files = []
        for root, dirs, files in os.walk(self.extracted_dir):
            for file in files:
                if file.endswith(('.java', '.smali')):
                    java_files.append(Path(root) / file)
        return java_files
    
    def analyze_file(self, file_path: Path) -> Dict:
        """Analyze a single file for Bluetooth-related content."""
        findings = {
            'uuids': [],
            'bluetooth_refs': [],
            'methods': []
        }
        
        try:
            with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
                content = f.read()
                
                # Find UUIDs (various formats)
                uuid_patterns = [
                    r'[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}',
                    r'0x[0-9a-fA-F]{4}',  # Short UUIDs
                    r'"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"'
                ]
                
                for pattern in uuid_patterns:
                    matches = re.findall(pattern, content, re.IGNORECASE)
                    for match in matches:
                        clean_uuid = match.strip('"').upper()
                        if clean_uuid not in findings['uuids']:
                            findings['uuids'].append(clean_uuid)
                
                # Find Bluetooth-related references
                for keyword in self.bluetooth_keywords:
                    if keyword.lower() in content.lower():
                        findings['bluetooth_refs'].append(keyword)
                
                # Find interesting method signatures
                method_patterns = [
                    r'(public|private|protected).*?(parse|read|write|notify|connect|discover).*?\(',
                    r'(public|private|protected).*?BloodPressure.*?\(',
                    r'(public|private|protected).*?GATT.*?\(',
                    r'(public|private|protected).*?BLE.*?\('
                ]
                
                for pattern in method_patterns:
                    matches = re.findall(pattern, content, re.IGNORECASE | re.MULTILINE)
                    findings['methods'].extend(matches)
                
        except Exception as e:
            print(f"⚠️  Could not analyze {file_path}: {e}")
        
        return findings
    
    def analyze_manifest(self) -> Dict:
        """Analyze AndroidManifest.xml for permissions and services."""
        manifest_path = self.extracted_dir / "AndroidManifest.xml"
        manifest_info = {
            'permissions': [],
            'services': [],
            'package_name': ''
        }
        
        if manifest_path.exists():
            try:
                with open(manifest_path, 'r', encoding='utf-8', errors='ignore') as f:
                    content = f.read()
                    
                    # Find permissions
                    permission_pattern = r'<uses-permission[^>]+android:name="([^"]+)"'
                    permissions = re.findall(permission_pattern, content)
                    manifest_info['permissions'] = permissions
                    
                    # Find package name
                    package_pattern = r'package="([^"]+)"'
                    package_match = re.search(package_pattern, content)
                    if package_match:
                        manifest_info['package_name'] = package_match.group(1)
                    
                    # Find services
                    service_pattern = r'<service[^>]+android:name="([^"]+)"'
                    services = re.findall(service_pattern, content)
                    manifest_info['services'] = services
                    
            except Exception as e:
                print(f"⚠️  Could not analyze manifest: {e}")
        
        return manifest_info
    
    def analyze_resources(self) -> Dict:
        """Analyze resource files for relevant strings and values."""
        resources_info = {
            'strings': {},
            'relevant_resources': []
        }
        
        # Look for strings.xml files
        for strings_file in self.extracted_dir.rglob("strings.xml"):
            try:
                with open(strings_file, 'r', encoding='utf-8', errors='ignore') as f:
                    content = f.read()
                    
                    # Find string resources
                    string_pattern = r'<string name="([^"]+)">([^<]+)</string>'
                    strings = re.findall(string_pattern, content)
                    
                    for name, value in strings:
                        # Look for Bluetooth-related strings
                        if any(keyword in name.lower() or keyword in value.lower() 
                               for keyword in self.bluetooth_keywords):
                            resources_info['strings'][name] = value
                            
            except Exception as e:
                print(f"⚠️  Could not analyze {strings_file}: {e}")
        
        return resources_info
    
    def run_analysis(self) -> Dict:
        """Run the complete analysis process."""
        print(f"🔍 Analyzing APK: {self.apk_path.name}")
        print("="*60)
        
        # Extract APK
        if not self.extract_apk():
            return self.findings
        
        # Analyze manifest
        print("📱 Analyzing AndroidManifest.xml...")
        manifest_info = self.analyze_manifest()
        self.findings['manifest'] = manifest_info
        
        # Analyze resources
        print("📄 Analyzing resources...")
        resources_info = self.analyze_resources()
        self.findings['resources'] = resources_info
        
        # Find and analyze Java/Smali files
        java_files = self.find_java_files()
        print(f"📚 Found {len(java_files)} code files to analyze...")
        
        bluetooth_files = []
        
        for file_path in java_files:
            file_findings = self.analyze_file(file_path)
            
            # Check if this file is Bluetooth-related
            is_bluetooth_file = (
                file_findings['bluetooth_refs'] or 
                file_findings['uuids'] or
                any(keyword in str(file_path).lower() for keyword in self.bluetooth_keywords)
            )
            
            if is_bluetooth_file:
                bluetooth_files.append(str(file_path))
                
                # Add UUIDs to global findings
                for uuid in file_findings['uuids']:
                    if uuid not in self.findings['uuids_found']:
                        self.findings['uuids_found'][uuid] = []
                    self.findings['uuids_found'][uuid].append(str(file_path))
                
                # Add methods to global findings
                self.findings['interesting_methods'].extend(file_findings['methods'])
        
        self.findings['bluetooth_classes'] = bluetooth_files
        
        return self.findings
    
    def generate_report(self) -> str:
        """Generate a comprehensive analysis report."""
        report = []
        report.append("# Qardio APK Analysis Report")
        report.append("="*50)
        report.append("")
        
        # Package Information
        if 'manifest' in self.findings:
            manifest = self.findings['manifest']
            report.append("## Package Information")
            report.append(f"**Package Name:** {manifest.get('package_name', 'Unknown')}")
            report.append("")
            
            if manifest.get('permissions'):
                report.append("**Permissions:**")
                for perm in manifest['permissions']:
                    if 'bluetooth' in perm.lower():
                        report.append(f"- ✅ {perm}")
                    else:
                        report.append(f"- {perm}")
                report.append("")
        
        # Bluetooth-Related Files
        if self.findings['bluetooth_classes']:
            report.append("## Bluetooth-Related Classes")
            report.append(f"Found {len(self.findings['bluetooth_classes'])} files:")
            for file_path in self.findings['bluetooth_classes']:
                report.append(f"- `{file_path}`")
            report.append("")
        
        # UUID Analysis
        if self.findings['uuids_found']:
            report.append("## UUID Analysis")
            
            standard_found = []
            custom_found = []
            
            for uuid, locations in self.findings['uuids_found'].items():
                if uuid.upper() in self.standard_uuids:
                    standard_found.append((uuid, self.standard_uuids[uuid.upper()], locations))
                else:
                    custom_found.append((uuid, locations))
            
            if standard_found:
                report.append("### Standard Bluetooth UUIDs Found:")
                for uuid, description, locations in standard_found:
                    report.append(f"- **{uuid}** - {description}")
                    for loc in locations[:3]:  # Limit to first 3 locations
                        report.append(f"  - Found in: `{loc}`")
                    if len(locations) > 3:
                        report.append(f"  - ... and {len(locations)-3} more files")
                report.append("")
            
            if custom_found:
                report.append("### Custom/Unknown UUIDs Found:")
                for uuid, locations in custom_found:
                    report.append(f"- **{uuid}**")
                    for loc in locations[:2]:  # Limit to first 2 locations
                        report.append(f"  - Found in: `{loc}`")
                report.append("")
        
        # Interesting Methods
        if self.findings['interesting_methods']:
            report.append("## Interesting Methods Found")
            unique_methods = list(set(self.findings['interesting_methods']))[:20]  # Limit output
            for method in unique_methods:
                if isinstance(method, tuple):
                    report.append(f"- `{' '.join(method)}`")
                else:
                    report.append(f"- `{method}`")
            if len(self.findings['interesting_methods']) > 20:
                report.append(f"- ... and {len(self.findings['interesting_methods'])-20} more methods")
            report.append("")
        
        # Resource Strings
        if 'resources' in self.findings and self.findings['resources']['strings']:
            report.append("## Relevant Resource Strings")
            for name, value in list(self.findings['resources']['strings'].items())[:10]:
                report.append(f"- **{name}:** {value}")
            report.append("")
        
        # Recommendations
        report.append("## Next Steps & Recommendations")
        report.append("")
        
        if self.findings['uuids_found']:
            report.append("### 1. Protocol Implementation Priority")
            
            has_standard_bp = any(
                "1810" in uuid or "2A35" in uuid 
                for uuid in self.findings['uuids_found'].keys()
            )
            
            if has_standard_bp:
                report.append("✅ **High Priority:** Standard Blood Pressure Profile detected")
                report.append("- Start with standard BLE Blood Pressure Service implementation")
                report.append("- Use UUIDs: 0x1810 (service), 0x2A35 (measurement), 0x2A49 (feature)")
            else:
                report.append("⚠️ **Custom Protocol:** No standard Blood Pressure UUIDs found")
                report.append("- Focus on custom UUID analysis")
                report.append("- May require deeper protocol reverse engineering")
        
        report.append("")
        report.append("### 2. Implementation Strategy")
        report.append("1. **Start with device discovery** using standard BLE scanning")
        report.append("2. **Connect and enumerate services** to validate UUIDs")
        report.append("3. **Enable notifications** on measurement characteristics")
        report.append("4. **Analyze data format** from captured packets")
        report.append("5. **Implement data parsing** based on Bluetooth specifications")
        
        report.append("")
        report.append("### 3. Files to Examine Closely")
        if self.findings['bluetooth_classes']:
            for file_path in self.findings['bluetooth_classes'][:5]:
                report.append(f"- `{file_path}`")
        
        return "\n".join(report)
    
    def save_findings(self, output_file: str = None):
        """Save findings to a JSON file."""
        if output_file is None:
            output_file = self.apk_path.with_suffix('.analysis.json')
        
        # Convert Path objects to strings for JSON serialization
        serializable_findings = json.loads(json.dumps(self.findings, default=str))
        
        with open(output_file, 'w') as f:
            json.dump(serializable_findings, f, indent=2)
        
        print(f"💾 Analysis saved to: {output_file}")

def main():
    parser = argparse.ArgumentParser(description='Analyze Qardio APK for Bluetooth protocol information')
    parser.add_argument('apk_path', help='Path to the Qardio APK file')
    parser.add_argument('--output', '-o', help='Output file for detailed findings (JSON)')
    parser.add_argument('--report', '-r', help='Output file for analysis report (Markdown)')
    
    args = parser.parse_args()
    
    if not os.path.exists(args.apk_path):
        print(f"❌ APK file not found: {args.apk_path}")
        sys.exit(1)
    
    # Run analysis
    analyzer = QardioAPKAnalyzer(args.apk_path)
    findings = analyzer.run_analysis()
    
    # Generate and display report
    report = analyzer.generate_report()
    print("\n" + report)
    
    # Save detailed findings
    if args.output:
        analyzer.save_findings(args.output)
    
    # Save report
    if args.report:
        with open(args.report, 'w') as f:
            f.write(report)
        print(f"📄 Report saved to: {args.report}")
    
    # Summary
    print("\n" + "="*60)
    print("🎯 ANALYSIS SUMMARY")
    print("="*60)
    print(f"Bluetooth-related files found: {len(findings['bluetooth_classes'])}")
    print(f"UUIDs discovered: {len(findings['uuids_found'])}")
    print(f"Interesting methods: {len(findings['interesting_methods'])}")
    
    standard_uuids = [
        uuid for uuid in findings['uuids_found'].keys() 
        if uuid.upper() in analyzer.standard_uuids
    ]
    
    if standard_uuids:
        print(f"✅ Standard Bluetooth UUIDs found: {len(standard_uuids)}")
        print("   This suggests the device uses standard BLE health profiles!")
    else:
        print("⚠️  No standard Bluetooth UUIDs found")
        print("   The device likely uses a custom protocol")

if __name__ == "__main__":
    main()