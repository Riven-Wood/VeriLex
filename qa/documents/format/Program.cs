#nullable enable
using System;
using System.IO;
using System.Linq;
using System.Text.Json;
using System.Xml.Linq;
using DocumentFormat.OpenXml.Packaging;
using DocumentFormat.OpenXml.Validation;
using Docxodus;
using Docxodus.Internal;

void Check(bool ok,string label){if(!ok)throw new Exception(label);}
void Validate(byte[] bytes){using var stream=new MemoryStream(bytes);using var doc=WordprocessingDocument.Open(stream,false);var errors=new OpenXmlValidator(DocumentFormat.OpenXml.FileFormatVersions.Office2016).Validate(doc).ToArray();Check(errors.Length==0,"OOXML: "+string.Join("; ",errors.Select(e=>e.Description)));}
XElement Body(byte[] bytes){using var stream=new MemoryStream(bytes);using var doc=WordprocessingDocument.Open(stream,false);return new XElement(doc.MainDocumentPart!.GetXDocument().Root!.Element(W.body)!);}
XElement Canonical(XElement element){var copy=new XElement(element);foreach(var attr in copy.DescendantsAndSelf().Attributes().Where(a=>a.Name==PtOpenXml.Unid||a.IsNamespaceDeclaration).ToList())attr.Remove();return copy;}
bool SameBody(byte[] left,byte[] right)=>XNode.DeepEquals(Canonical(Body(left)),Canonical(Body(right)));
string[] Ids(DocxSession session)=>session.Project().AnchorIndex.Values.Where(a=>a.Anchor.Kind=="p"&&a.Anchor.Scope=="body").Select(a=>a.Anchor.Id).ToArray();
byte[] Fixture(){using var stream=new MemoryStream();stream.Write(DocxSession.CreateBlankDocxBytes());using(var doc=WordprocessingDocument.Open(stream,true)){
 var body=doc.MainDocumentPart!.GetXDocument().Root!.Element(W.body)!;body.Elements(W.p).Remove();
 var paragraphs=new[]{new XElement(W.p,new XElement(W.pPr,new XElement(W.pStyle,new XAttribute(W.val,"FormatFixture"))),new XElement(W.r,new XElement(W.t,"虚构段落甲：合同条款。"))),new XElement(W.p,new XElement(W.r,new XElement(W.t,"虚构段落乙：第二条。"))),new XElement(W.p,new XElement(W.r,new XElement(W.t,"保持不变的末段。")))};
 body.AddFirst(paragraphs);doc.MainDocumentPart.PutXDocument();
 var styles=doc.MainDocumentPart.StyleDefinitionsPart!.GetXDocument();styles.Root!.Add(new XElement(W.style,new XAttribute(W.type,"paragraph"),new XAttribute(W.styleId,"FormatFixture"),new XElement(W.name,new XAttribute(W.val,"验收样式")),new XElement(W.basedOn,new XAttribute(W.val,"Normal")),new XElement(W.pPr,new XElement(W.spacing,new XAttribute(W.before,120),new XAttribute(W.after,180),new XAttribute(W.line,360),new XAttribute(W.lineRule,"auto")),new XElement(W.ind,new XAttribute(W.left,360),new XAttribute(W.right,180),new XAttribute(W.firstLine,240)))));doc.MainDocumentPart.StyleDefinitionsPart.PutXDocument();
 }return stream.ToArray();}
var bytes=Fixture();Validate(bytes);if(args.Length>0)File.WriteAllBytes(args[0],bytes);
using(var session=new DocxSession(bytes)){
 var format=session.GetFormatting(Ids(session)[0])!;Check(format.DirectParagraph.LeftIndentTwips is null,"direct style properties absent");Check(format.EffectiveParagraph.LeftIndentTwips==360&&format.EffectiveParagraph.RightIndentTwips==180&&format.EffectiveParagraph.FirstLineIndentTwips==240,"effective inherited indent");Check(format.EffectiveParagraph.SpacingBeforeTwips==120&&format.EffectiveParagraph.LineSpacing==360,"effective inherited spacing");
 var json=DocxSessionJson.SerializeFormattingInspectionOrNull(format);using var parsed=JsonDocument.Parse(json);Check(parsed.RootElement.GetProperty("effectiveParagraph").GetProperty("lineSpacingRule").GetString()=="auto","bridge formatting JSON");Console.WriteLine(json);
}
foreach(var tracked in new[]{false,true})foreach(var accept in new[]{false,true}){
 using var session=new DocxSession(bytes,new DocxSessionSettings{TrackedChanges=tracked?TrackedChangeMode.RenderInline:TrackedChangeMode.Accept,RevisionAuthor="段落验收"});var id=Ids(session)[0];
 foreach(var op in new[]{new ParagraphFormatOp{LeftIndent=720,RightIndent=360,SpacingBefore=240,SpacingAfter=120},new ParagraphFormatOp{LineSpacing=480,LineSpacingRule=LineSpacingRule.Auto,FirstLineIndent=360},new ParagraphFormatOp{HangingIndent=240},new ParagraphFormatOp{Alignment=ParagraphAlignment.Justify,LineSpacing=400,LineSpacingRule=LineSpacingRule.Exact}}){
  var before=session.Save();var result=session.SetParagraphFormat(id,op);Check(result.Success,result.Error?.Message??"apply paragraph format");var after=session.Save();Validate(after);Check(session.Undo(),"paragraph undo");Check(SameBody(before,session.Save()),"paragraph undo restores body");Check(session.Redo(),"paragraph redo");Check(SameBody(after,session.Save()),"paragraph redo restores body");
 }
 var changed=session.Save();var body=Body(changed);Check(body.Descendants(W.pPrChange).Count()==(tracked?1:0),"one same-author paragraph revision");Check(XNode.DeepEquals(Canonical(body.Elements(W.p).Last()),Canonical(Body(bytes).Elements(W.p).Last())),"unselected paragraph unchanged");
 using var reopened=new DocxSession(changed,new DocxSessionSettings{TrackedChanges=tracked?TrackedChangeMode.RenderInline:TrackedChangeMode.Accept,RevisionAuthor="段落验收"});var fresh=Ids(reopened)[0];var format=reopened.GetFormatting(fresh)!.EffectiveParagraph;Check(format.Alignment==ParagraphAlignment.Justify&&format.LineSpacing==400&&format.LineSpacingRule==LineSpacingRule.Exact&&format.HangingIndentTwips==240&&format.LeftIndentTwips==720&&format.RightIndentTwips==360,"format survives reopen");Check(reopened.SetParagraphFormat(fresh,new ParagraphFormatOp{SpacingBefore=0,LineSpacing=480,LineSpacingRule=LineSpacingRule.AtLeast}).Success,"same author continues saved revision");
 if(tracked){Check((accept?reopened.AcceptAllRevisions():reopened.RejectAllRevisions()).Success,"resolve paragraph revision");if(!accept)Check(SameBody(bytes,reopened.Save()),"reject all restores before-image");}
 Validate(reopened.Save());if(!tracked||accept){var f=reopened.GetFormatting(Ids(reopened)[0])!.EffectiveParagraph;Check(f.SpacingBeforeTwips==0&&f.LineSpacingRule==LineSpacingRule.AtLeast&&f.LineSpacing==480,"accepted final formatting");}
}
using(var author=new DocxSession(bytes,new DocxSessionSettings{TrackedChanges=TrackedChangeMode.RenderInline,RevisionAuthor="他人"})){
 Check(author.SetParagraphFormat(Ids(author)[0],new ParagraphFormatOp{SpacingBefore=300}).Success,"other author fixture");var pending=author.Save();using var session=new DocxSession(pending,new DocxSessionSettings{TrackedChanges=TrackedChangeMode.RenderInline,RevisionAuthor="段落验收"});var result=session.SetParagraphFormat(Ids(session)[0],new ParagraphFormatOp{SpacingAfter=300});Check(!result.Success&&result.Error?.Code==EditErrorCode.UnresolvedStructuralRevision,"refuse overwriting other's pending paragraph revision");Check(SameBody(pending,session.Save()),"other reviewer refusal is unchanged");Check(session.RejectAllRevisions().Success,"explicitly reject prior author");Check(SameBody(bytes,session.Save()),"prior reviewer rejection restores original");
}
foreach(var tracked in new[]{false,true})foreach(var op in new[]{new ParagraphFormatOp{LeftIndent=0,IndentDelta=1},new ParagraphFormatOp{FirstLineIndent=-1},new ParagraphFormatOp{HangingIndent=-1},new ParagraphFormatOp{SpacingBefore=-1},new ParagraphFormatOp{SpacingAfter=-1},new ParagraphFormatOp{LineSpacing=-1},new ParagraphFormatOp{FirstLineIndent=1,HangingIndent=1},new ParagraphFormatOp{LineSpacingRule=LineSpacingRule.Exact}}){using var session=new DocxSession(bytes,new DocxSessionSettings{TrackedChanges=tracked?TrackedChangeMode.RenderInline:TrackedChangeMode.Accept});var result=session.SetParagraphFormat(Ids(session)[0],op);Check(!result.Success&&result.Error?.Code==EditErrorCode.InvalidParagraphFormat,"reject invalid paragraph input");Check(SameBody(bytes,session.Save()),"invalid format leaves body unchanged");Check(!session.Undo(),"invalid format has no undo mutation");}
foreach(var tracked in new[]{false,true}){
 using var session=new DocxSession(bytes,new DocxSessionSettings{TrackedChanges=tracked?TrackedChangeMode.RenderInline:TrackedChangeMode.Accept,RevisionAuthor="段落验收"});var ids=Ids(session);
 using(var tx=session.BeginTransaction()){foreach(var id in ids.Take(2))Check(session.SetParagraphFormat(id,new ParagraphFormatOp{Alignment=ParagraphAlignment.Justify,SpacingBefore=100}).Success,"multi paragraph apply");tx.Commit();}
 Check(session.Undo(),"single undo for paragraph batch");Check(SameBody(bytes,session.Save()),"one undo restores whole batch");Check(session.Redo(),"batch redo");Validate(session.Save());
 var before=session.Save();using(var tx=session.BeginTransaction()){Check(session.SetParagraphFormat(ids[0],new ParagraphFormatOp{SpacingAfter=200}).Success,"batch first paragraph");Check(!session.SetParagraphFormat(ids[1],new ParagraphFormatOp{SpacingAfter=-1}).Success,"batch second rejects");}Check(SameBody(before,session.Save()),"failed multi-paragraph batch rolls back");
}
var parsedOp=DocxSessionJson.ParseParagraphFormatOp("{\"alignment\":\"justify\",\"leftIndent\":720,\"rightIndent\":360,\"spacingBefore\":120,\"lineSpacing\":360,\"lineSpacingRule\":\"atLeast\"}");Check(parsedOp.Alignment==ParagraphAlignment.Justify&&parsedOp.LeftIndent==720&&parsedOp.RightIndent==360&&parsedOp.SpacingBefore==120&&parsedOp.LineSpacingRule==LineSpacingRule.AtLeast,"JSON unit and enum contract");

byte[] PropertyFixture(bool inherited, bool rtl, XElement indent, XElement? spacing=null){
 using var stream=new MemoryStream();stream.Write(bytes);
 using(var doc=WordprocessingDocument.Open(stream,true)){
  var root=doc.MainDocumentPart!.GetXDocument().Root!;
  var direct=root.Element(W.body)!.Elements(W.p).First().Element(W.pPr)!;
  var styles=doc.MainDocumentPart.StyleDefinitionsPart!.GetXDocument();
  var style=styles.Root!.Elements(W.style).First(x=>(string?)x.Attribute(W.styleId)=="FormatFixture").Element(W.pPr)!;
  style.Elements(W.ind).Remove();style.Elements(W.spacing).Remove();
  var target=inherited?style:direct;if(spacing!=null)target.Add(new XElement(spacing));target.Add(new XElement(indent));
  if(rtl)style.AddFirst(new XElement(W.bidi));
  doc.MainDocumentPart.PutXDocument();doc.MainDocumentPart.StyleDefinitionsPart.PutXDocument();
 }
 return stream.ToArray();
}
XElement EffectiveProperties(byte[] document){using var stream=new MemoryStream(document);using var doc=WordprocessingDocument.Open(stream,false);return FormattingAssembler.ResolveEffectiveParagraphProperties(doc,doc.MainDocumentPart!.GetXDocument().Root!.Element(W.body)!.Elements(W.p).First());}
foreach(var inherited in new[]{false,true})foreach(var rtl in new[]{false,true}){
 var source=PropertyFixture(inherited,rtl,new XElement(W.ind,new XAttribute(W.w+"start",900),new XAttribute(W.w+"end",300)));
 using var session=new DocxSession(source,new DocxSessionSettings{TrackedChanges=TrackedChangeMode.RenderInline,RevisionAuthor="段落验收"});
 Check(session.SetParagraphFormat(Ids(session)[0],new ParagraphFormatOp{LeftIndent=720}).Success,"logical left indent apply");
 var result=session.Save();Validate(result);var effective=EffectiveProperties(result);Console.WriteLine($"LOGICAL inherited={inherited} rtl={rtl}: {effective.Element(W.ind)}");
 Check(session.GetFormatting(Ids(session)[0])!.EffectiveParagraph.LeftIndentTwips==720,"logical left readback");Check(session.GetFormatting(Ids(session)[0])!.DirectParagraph.LeftIndentTwips==720,"direct logical left honors inherited bidi");
 Check(session.GetFormatting(Ids(session)[0])!.EffectiveParagraph.RightIndentTwips==(rtl?900:300),"opposite direct logical indent preserved");
 Check(session.RejectAllRevisions().Success&&SameBody(source,session.Save()),"logical formatting rejection restores before image");
}
foreach(var inherited in new[]{false,true}){
 var source=PropertyFixture(inherited,false,new XElement(W.ind,new XAttribute(W.w+"leftChars",200),new XAttribute(W.w+"rightChars",100),new XAttribute(W.w+"firstLineChars",200)),new XElement(W.spacing,new XAttribute(W.w+"beforeLines",200),new XAttribute(W.w+"afterLines",100),new XAttribute(W.beforeAutospacing,"1"),new XAttribute(W.afterAutospacing,"1")));
 using var session=new DocxSession(source,new DocxSessionSettings{TrackedChanges=TrackedChangeMode.RenderInline,RevisionAuthor="段落验收"});
 var originalFormat=session.GetFormatting(Ids(session)[0])!;
 Check(originalFormat.EffectiveParagraph.LeftIndentCharacterUnits==200&&originalFormat.EffectiveParagraph.RightIndentCharacterUnits==100&&originalFormat.EffectiveParagraph.FirstLineIndentCharacterUnits==200,"read character indent metadata");
 Check(originalFormat.EffectiveParagraph.SpacingBeforeLineUnits==200&&originalFormat.EffectiveParagraph.SpacingAfterLineUnits==100&&originalFormat.EffectiveParagraph.SpacingBeforeAuto==true&&originalFormat.EffectiveParagraph.SpacingAfterAuto==true,"read line-unit and auto metadata");
 var originalJson=DocxSessionJson.SerializeFormattingInspectionOrNull(originalFormat);using(var json=JsonDocument.Parse(originalJson)){var p=json.RootElement.GetProperty("effectiveParagraph");Check(p.GetProperty("leftIndentCharacterUnits").GetInt32()==200&&p.GetProperty("firstLineIndentCharacterUnits").GetInt32()==200&&p.GetProperty("spacingBeforeLineUnits").GetInt32()==200&&p.GetProperty("spacingBeforeAuto").GetBoolean(),"serialize original unit metadata");}Console.WriteLine("UNIT_JSON "+originalJson);
 Check(session.SetParagraphFormat(Ids(session)[0],new ParagraphFormatOp{LeftIndent=720,RightIndent=360,FirstLineIndent=240,SpacingBefore=120,SpacingAfter=120}).Success,"character and line unit override apply");
 var finalFormat=session.GetFormatting(Ids(session)[0])!.EffectiveParagraph;Check(finalFormat.LeftIndentCharacterUnits==0&&finalFormat.FirstLineIndentCharacterUnits==0&&finalFormat.SpacingBeforeLineUnits==0&&finalFormat.SpacingBeforeAuto==false,"explicit override units disabled in inspection");
 var result=session.Save();Validate(result);var effective=EffectiveProperties(result);Console.WriteLine($"CHARACTER inherited={inherited}: {effective}");
 Check(effective.Element(W.ind)!.Attributes().Where(a=>a.Name.LocalName.EndsWith("Chars")).All(a=>a.Value=="0"),"character indent disabled");Check(effective.Element(W.spacing)!.Attributes().Where(a=>a.Name.LocalName.EndsWith("Lines")||a.Name.LocalName.EndsWith("Autospacing")).All(a=>a.Value=="0"),"line-unit and auto spacing disabled");
 Check(session.RejectAllRevisions().Success&&SameBody(source,session.Save()),"character formatting rejection restores before image");
}


foreach(var tracked in new[]{false,true}){
 using var session=new DocxSession(bytes,new DocxSessionSettings{TrackedChanges=tracked?TrackedChangeMode.RenderInline:TrackedChangeMode.Accept});var id=Ids(session)[0];
 Check(session.SetParagraphFormat(id,new ParagraphFormatOp{LeftIndent=-720,RightIndent=-360}).Success,"negative physical indents accepted");Validate(session.Save());
 var f=session.GetFormatting(id)!.EffectiveParagraph;Check(f.LeftIndentTwips==-720&&f.RightIndentTwips==-360,"negative physical readback");
}
byte[] ComplexInheritanceFixture(bool inTable){
 using var stream=new MemoryStream();stream.Write(bytes);using(var doc=WordprocessingDocument.Open(stream,true)){
  var body=doc.MainDocumentPart!.GetXDocument().Root!.Element(W.body)!;var p=body.Elements(W.p).First();
  if(inTable){
   var styles=doc.MainDocumentPart.StyleDefinitionsPart!.GetXDocument();
   styles.Root!.Add(new XElement(W.style,new XAttribute(W.type,"table"),new XAttribute(W.styleId,"TableBase"),new XElement(W.name,new XAttribute(W.val,"表样式基类")),new XElement(W.tblStylePr,new XAttribute(W.type,"firstRow"),new XElement(W.pPr,new XElement(W.spacing,new XAttribute(W.before,600))))));
   styles.Root.Add(new XElement(W.style,new XAttribute(W.type,"table"),new XAttribute(W.styleId,"TableFixture"),new XElement(W.name,new XAttribute(W.val,"表样式")),new XElement(W.basedOn,new XAttribute(W.val,"TableBase"))));
   doc.MainDocumentPart.StyleDefinitionsPart.PutXDocument();p.Remove();
   body.AddFirst(new XElement(W.tbl,new XElement(W.tblPr,new XElement(W.tblStyle,new XAttribute(W.val,"TableFixture"))),new XElement(W.tblGrid,new XElement(W.gridCol,new XAttribute(W.w+"w",6000))),new XElement(W.tr,new XElement(W.tc,p))));
  }else{
   var pPr=p.Element(W.pPr)!;pPr.Add(new XElement(W.numPr,new XElement(W.ilvl,new XAttribute(W.val,0)),new XElement(W.numId,new XAttribute(W.val,7))));
   var numbering=doc.MainDocumentPart.NumberingDefinitionsPart??doc.MainDocumentPart.AddNewPart<NumberingDefinitionsPart>();
   numbering.PutXDocument(new XDocument(new XElement(W.numbering,new XElement(W.abstractNum,new XAttribute(W.abstractNumId,7),new XElement(W.lvl,new XAttribute(W.ilvl,0),new XElement(W.start,new XAttribute(W.val,1)),new XElement(W.numFmt,new XAttribute(W.val,"decimal")),new XElement(W.lvlText,new XAttribute(W.val,"%1.")),new XElement(W.pPr,new XElement(W.ind,new XAttribute(W.w+"leftChars",200))))),new XElement(W.num,new XAttribute(W.numId,7),new XElement(W.abstractNumId,new XAttribute(W.val,7))))));
  }
  doc.MainDocumentPart.PutXDocument();
 }return stream.ToArray();
}
foreach(var inTable in new[]{false,true}){
 var source=ComplexInheritanceFixture(inTable);Validate(source);using var session=new DocxSession(source);
 var id=session.Project().AnchorIndex.Values.Where(a=>a.Anchor.Kind is "p" or "h" or "li").Select(a=>a.Anchor.Id).First(x=>session.Raw.GetXml(x).Contains("虚构段落甲"));var f=session.GetFormatting(id)!;
 Check(f.UnresolvedInheritedFormatting,inTable?"inherited conditional table-style flag":"numbering-level inheritance flag");
 using(var json=JsonDocument.Parse(DocxSessionJson.SerializeFormattingInspectionOrNull(f)))Check(json.RootElement.GetProperty("unresolvedInheritedFormatting").GetBoolean(),"serialize incomplete inheritance flag");
 Check(session.SetParagraphFormat(id,new ParagraphFormatOp{LeftIndent=720,SpacingBefore=120}).Success,"explicit formatting despite unresolved inheritance");
 f=session.GetFormatting(id)!;Check(f.DirectParagraph.LeftIndentTwips==720&&f.DirectParagraph.SpacingBeforeTwips==120&&f.DirectParagraph.LeftIndentCharacterUnits==0,"direct values remain trustworthy after explicit override");Validate(session.Save());
}
// Direct inspection must not invent a rule: UI needs to distinguish an explicit
// auto choice from an unresolved inherited rule when only w:line is written.
foreach(var inheritedRule in new string?[]{null,"auto","exact"})foreach(var directRule in new string?[]{null,"auto","exact"})foreach(var writeLine in new[]{false,true}){
 using var stream=new MemoryStream();stream.Write(bytes);
 using(var document=WordprocessingDocument.Open(stream,true)){
  var styles=document.MainDocumentPart!.StyleDefinitionsPart!.GetXDocument();
  var inheritedSpacing=styles.Root!.Elements(W.style).First(x=>(string?)x.Attribute(W.styleId)=="FormatFixture").Element(W.pPr)!.Element(W.spacing)!;
  inheritedSpacing.SetAttributeValue(W.lineRule,inheritedRule);
  var spacing=new XElement(W.spacing);if(writeLine)spacing.SetAttributeValue(W.line,480);if(directRule!=null)spacing.SetAttributeValue(W.lineRule,directRule);
  document.MainDocumentPart.GetXDocument().Root!.Element(W.body)!.Elements(W.p).First().Element(W.pPr)!.Add(spacing);
  document.MainDocumentPart.PutXDocument();document.MainDocumentPart.StyleDefinitionsPart.PutXDocument();
 }
 var source=stream.ToArray();Validate(source);using var session=new DocxSession(source);
 var f=session.GetFormatting(Ids(session)[0])!;
 var expectedDirect=directRule==null?(LineSpacingRule?)null:directRule=="auto"?LineSpacingRule.Auto:LineSpacingRule.Exact;
 Check(f.DirectParagraph.LineSpacingRule==expectedDirect,"direct rule preserves explicit absence");
 Check(f.DirectParagraph.LineSpacing==(writeLine?480:null),"direct line preserves explicit absence");
 Check(f.EffectiveParagraph.LineSpacingRule==((directRule??inheritedRule??"auto")=="auto"?LineSpacingRule.Auto:LineSpacingRule.Exact),"effective rule retains inheritance or auto default");
 using var json=JsonDocument.Parse(DocxSessionJson.SerializeFormattingInspectionOrNull(f));
 var direct=json.RootElement.GetProperty("directParagraph");
 Check(direct.TryGetProperty("lineSpacingRule",out var rule)==(directRule!=null),"bridge JSON does not invent direct rule");
 if(directRule!=null)Check(rule.GetString()==directRule,"bridge JSON explicit rule value");
 Check(json.RootElement.GetProperty("effectiveParagraph").GetProperty("lineSpacingRule").GetString()==(directRule??inheritedRule??"auto"),"bridge JSON effective rule value");
 Check(SameBody(source,session.Save()),"format inspection does not mutate document");
}
{
 var source=ComplexInheritanceFixture(false);using var stream=new MemoryStream();stream.Write(source);
 using(var document=WordprocessingDocument.Open(stream,true)){
  document.MainDocumentPart!.GetXDocument().Root!.Element(W.body)!.Elements(W.p).First().Element(W.pPr)!.Add(new XElement(W.spacing,new XAttribute(W.line,480)));
  document.MainDocumentPart.PutXDocument();
 }
 source=stream.ToArray();Validate(source);using var session=new DocxSession(source);var id=session.Project().AnchorIndex.Values.First(a=>a.Anchor.Kind=="li").Anchor.Id;
 var format=session.GetFormatting(id)!;Check(format.UnresolvedInheritedFormatting&&format.DirectParagraph.LineSpacing==480&&format.DirectParagraph.LineSpacingRule==null,"unresolved numbered paragraph cannot claim explicit auto");
 using var json=JsonDocument.Parse(DocxSessionJson.SerializeFormattingInspectionOrNull(format));Check(!json.RootElement.GetProperty("directParagraph").TryGetProperty("lineSpacingRule",out _),"numbered paragraph JSON omits unknown rule");
}
Console.WriteLine("PASS paragraph engine: inherited readback, all spacing/indent modes, same/other author, accept/reject, undo/redo, reopen, invalid input and batch rollback");
