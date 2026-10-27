using System;
using System.IO;
using System.Linq;
using System.Xml.Linq;
using DocumentFormat.OpenXml.Packaging;
using DocumentFormat.OpenXml.Validation;
using Docxodus;

void Check(bool ok,string message){if(!ok)throw new Exception(message);}
void Validate(byte[] bytes){using var doc=WordprocessingDocument.Open(new MemoryStream(bytes),false);var errors=new OpenXmlValidator(DocumentFormat.OpenXml.FileFormatVersions.Office2016).Validate(doc).ToArray();Check(errors.Length==0,string.Join(";",errors.Select(e=>e.Description)));}
var original=File.ReadAllBytes(args[0]);
using(var session=new DocxSession(original,new DocxSessionSettings{TrackedChanges=TrackedChangeMode.RenderInline,RevisionAuthor="格式验收"})){
 var matches=session.Grep("甲方");Check(matches.Count==3,"search includes body and table");
 using(var transaction=session.BeginTransaction()){
  foreach(var match in matches.Reverse())Check(session.ReplaceTextAtSpan(match.EnclosingAnchor.Anchor.Id,match.Span.Start,match.Span.Length,"委托方").Success,"replace literal span");
  transaction.Commit();
 }
 Check(session.Grep("甲方").Count==0,"deleted revisions excluded from search");Check(session.Grep("委托方").Count==3,"replacement count");Validate(session.Save());
 Check(session.Undo(),"batch undo");Check(session.Grep("甲方").Count==3,"one undo restores all replacements");
 Check(session.Redo(),"batch redo");Check(session.Grep("委托方").Count==3,"one redo restores all replacements");
 using(var reopened=new DocxSession(session.Save())){Check(reopened.RejectAllRevisions().Success,"reject replacement revisions");Check(reopened.Grep("甲方").Count==3,"reject restores original");Validate(reopened.Save());}
}
using(var session=new DocxSession(original)){
 var match=session.Grep("甲方")[0];
 using(var transaction=session.BeginTransaction()){
  Check(session.ReplaceTextAtSpan(match.EnclosingAnchor.Anchor.Id,0,2,"临时").Success,"partial mutation");
  transaction.Rollback();
 }
 Check(session.Grep("甲方").Count==3,"batch failure rollback");
 Check(session.ApplyFormat(match.EnclosingAnchor.Anchor.Id,match.Span,new FormatOp{FontFamily="仿宋",FontSizePts=14}).Success,"Chinese formatting");
 using var doc=WordprocessingDocument.Open(new MemoryStream(session.Save()),false);
 var font=doc.MainDocumentPart!.GetXDocument().Descendants(W.rFonts).First();
 Check((string?)font.Attribute(W.eastAsia)=="仿宋","Chinese source font must be written to eastAsia, not only Latin font slots");
 Validate(session.Save());
}
using(var session=new DocxSession(original,new DocxSessionSettings{TrackedChanges=TrackedChangeMode.RenderInline,RevisionAuthor="格式验收"})){
 var match=session.Grep("粘贴位置")[0];var id=match.EnclosingAnchor.Anchor.Id;
 Check(session.SetParagraphFormat(id,new ParagraphFormatOp{Alignment=ParagraphAlignment.Center}).Success,"format before multi paragraph paste");
 Check(session.SplitParagraph(id,5).Success,"split formatted paragraph");Validate(session.Save());
 Check(session.RejectAllRevisions().Success,"reject split and paragraph formatting");Validate(session.Save());
}
if(args.Length>1){
 var edited=File.ReadAllBytes(args[1]);Validate(edited);
 using var accepted=new DocxSession(edited);Check(accepted.AcceptAllRevisions().Success,"accept clipboard revisions");Validate(accepted.Save());
 File.WriteAllBytes(args[1]+".accepted.docx",accepted.Save());
}
Console.WriteLine("PASS search, tracked batch replace, undo/redo, rollback, Chinese fonts and Word package validation");
